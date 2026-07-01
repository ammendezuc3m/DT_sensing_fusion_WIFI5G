function out = ssb_process_capture(waveform, rx, params, precomp)
%SSB_PROCESS_CAPTURE Procesa una captura SDR y devuelve H SSB complejo.
%
% Devuelve:
%   out.hSSB       -> 240 x 4 canal complejo estimado
%   out.rxGridSSB  -> 240 x 4 grid recibido del SSB seleccionado
%   out.gridFull   -> 360 x 6 grid ampliado para debug
%   out.gridUseful -> 240 x 6 parte util centrada dentro de gridFull
%   out.ibar_SSB   -> indice SSB estimado
%   out.ncellid    -> Physical Cell ID
%   out.freqOffset -> offset frecuencia
%   out.noiseVar   -> varianza ruido estimada
%   out.valid      -> true/false

    out = struct();
    out.valid = false;
    out.error = "";
    out.hSSB = [];
    out.rxGridSSB = [];
    out.gridFull = [];
    out.gridUseful = [];

    try
        % -------------------------
        % 1) Correccion de frecuencia + NID2
        % -------------------------
        [correctedWaveform, freqOffset, NID2] = mySSBurstFrequencyCorrectFast( ...
            waveform, ...
            params.ssbBlockPattern, ...
            rx.SampleRate, ...
            params.searchBW, ...
            params.displayFigure);

        out.freqOffset = freqOffset;
        out.NID2 = NID2;

        % -------------------------
        % 2) Preparacion GPU/CPU
        % -------------------------
        if params.useGPU
            correctedWaveform_g = gpuArray(correctedWaveform);

            refGridTim_g = gpuArray.zeros([params.nrbSSB*12 2]);
            refGridTim_g(nrPSSIndices, 2) = nrPSS(NID2);

            timingOffset = nrTimingEstimate( ...
                correctedWaveform_g, ...
                params.nrbSSB, ...
                params.scsNumeric, ...
                params.nSlot, ...
                refGridTim_g, ...
                SampleRate=rx.SampleRate);

            wait(precomp.gpu);

            timingOffsetCPU = gather(timingOffset);
            correctedWaveform_g = correctedWaveform_g(1+timingOffset:end, :);

            rxGrid20_g = nrOFDMDemodulate( ...
                correctedWaveform_g, ...
                params.nrbSSB, ...
                params.scsNumeric, ...
                params.nSlot, ...
                SampleRate=rx.SampleRate);

            wait(precomp.gpu);

            nSym = size(rxGrid20_g, 2);
            if nSym >= 5
                rxGridSSB_g = rxGrid20_g(:, 2:5, :);
                out.ssbSymbolSelection = "2:5";
            elseif nSym >= 4
                rxGridSSB_g = rxGrid20_g(:, 1:4, :);
                out.ssbSymbolSelection = "1:4";
            else
                out.error = sprintf("Demodulacion con menos de 4 simbolos: %d", nSym);
                return;
            end

            % -------------------------
            % 3) Deteccion SSS vectorizada
            % -------------------------
            sssIndices = nrSSSIndices;
            sssRx_g = nrExtractResources(sssIndices, rxGridSSB_g);
            sssRx_g = sssRx_g(:);

            sssRefMat_g = precomp.sssRefAll{NID2+1};
            sssEst_g = abs((sssRx_g' * conj(sssRefMat_g))).^2;

            [~, idx] = max(gather(sssEst_g));
            NID1 = idx - 1;
            ncellid = 3*NID1 + NID2;

            wait(precomp.gpu);

            rxGridSSB = gather(rxGridSSB_g);

        else
            refGridTim = zeros([params.nrbSSB*12 2]);
            refGridTim(nrPSSIndices, 2) = nrPSS(NID2);

            timingOffsetCPU = nrTimingEstimate( ...
                correctedWaveform, ...
                params.nrbSSB, ...
                params.scsNumeric, ...
                params.nSlot, ...
                refGridTim, ...
                SampleRate=rx.SampleRate);

            correctedWaveform = correctedWaveform(1+timingOffsetCPU:end, :);

            rxGrid20 = nrOFDMDemodulate( ...
                correctedWaveform, ...
                params.nrbSSB, ...
                params.scsNumeric, ...
                params.nSlot, ...
                SampleRate=rx.SampleRate);

            nSym = size(rxGrid20, 2);
            if nSym >= 5
                rxGridSSB = rxGrid20(:, 2:5, :);
                out.ssbSymbolSelection = "2:5";
            elseif nSym >= 4
                rxGridSSB = rxGrid20(:, 1:4, :);
                out.ssbSymbolSelection = "1:4";
            else
                out.error = sprintf("Demodulacion con menos de 4 simbolos: %d", nSym);
                return;
            end

            sssIndices = nrSSSIndices;
            sssRx = nrExtractResources(sssIndices, rxGridSSB);
            sssRx = sssRx(:);

            sssRefMat = precomp.sssRefAll{NID2+1};
            sssEst = abs((sssRx' * conj(sssRefMat))).^2;

            [~, idx] = max(sssEst);
            NID1 = idx - 1;
            ncellid = 3*NID1 + NID2;
        end

        out.timingOffset = timingOffsetCPU;
        out.NID1 = NID1;
        out.ncellid = ncellid;

        % -------------------------
        % 4) Escaneo PBCH-DMRS para ibar_SSB
        % -------------------------
        dmrsIndices = nrPBCHDMRSIndices(ncellid);
        dmrsEst = nan(1, 8);

        for ibar = 0:7
            refGridDMRS = zeros([params.nrbSSB*12 4]);
            refGridDMRS(dmrsIndices) = nrPBCHDMRS(ncellid, ibar);

            [hTmp, nTmp] = nrChannelEstimate( ...
                rxGridSSB, ...
                refGridDMRS, ...
                'AveragingWindow', [0 1]);

            sigPow = mean(abs(hTmp(:)).^2);
            if nTmp <= 0
                dmrsEst(ibar+1) = -Inf;
            else
                dmrsEst(ibar+1) = 10*log10(sigPow / nTmp);
            end
        end

        [~, bestIdx] = max(dmrsEst);
        ibar_SSB = bestIdx - 1;

        out.dmrsEst = dmrsEst;
        out.ibar_SSB = ibar_SSB;

        % -------------------------
        % 5) Estimacion final de canal H(k,l)
        % -------------------------
        if params.useGPU
            refGrid3_g = gpuArray.zeros([params.nrbSSB*12 4]);
            refGrid3_g(dmrsIndices) = nrPBCHDMRS(ncellid, ibar_SSB);
            refGrid3_g(sssIndices) = nrSSS(ncellid);

            [hest_g, nest_g] = nrChannelEstimate( ...
                rxGridSSB_g, ...
                refGrid3_g, ...
                'AveragingWindow', [0 1]);

            wait(precomp.gpu);

            hest = gather(hest_g);
            nest = gather(nest_g);

            correctedWaveformAligned = gather(correctedWaveform_g);
        else
            refGrid3 = zeros([params.nrbSSB*12 4]);
            refGrid3(dmrsIndices) = nrPBCHDMRS(ncellid, ibar_SSB);
            refGrid3(sssIndices) = nrSSS(ncellid);

            [hest, nest] = nrChannelEstimate( ...
                rxGridSSB, ...
                refGrid3, ...
                'AveragingWindow', [0 1]);

            correctedWaveformAligned = correctedWaveform;
        end

        out.noiseVar = nest;
        out.hSSB = single(hest(:, :, 1));
        out.rxGridSSB = single(rxGridSSB(:, :, 1));

        % -------------------------
        % 6) Grid ampliado 360 x 6 para debug/visualizacion
        % -------------------------
        rxGridSave = nrOFDMDemodulate( ...
            correctedWaveformAligned, ...
            params.demodRB, ...
            params.scsNumeric, ...
            params.nSlot, ...
            SampleRate=rx.SampleRate);

        nKeep = min(6, size(rxGridSave, 2));
        tmp = zeros(size(rxGridSave,1), 6, size(rxGridSave,3), 'like', rxGridSave);
        tmp(:, 1:nKeep, :) = rxGridSave(:, 1:nKeep, :);

        ssbStart = 12*(params.demodRB - params.nrbSSB)/2 + 1;
        ssbRows = ssbStart:ssbStart + 12*params.nrbSSB - 1;

        out.gridFull = single(tmp(:, 1:6, 1));
        out.gridUseful = single(tmp(ssbRows, 1:6, 1));

        % -------------------------
        % 7) Info final
        % -------------------------
        out.valid = true;
        out.timestamp = char(datetime("now", "TimeZone", "local", "Format", "yyyy-MM-dd'T'HH:mm:ss.SSS"));
        out.centerFrequency = rx.CenterFrequency;
        out.sampleRate = rx.SampleRate;

    catch ME
        out.valid = false;
        out.error = string(ME.message);
    end
end

