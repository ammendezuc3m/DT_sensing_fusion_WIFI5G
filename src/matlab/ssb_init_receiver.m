function [rx, params, precomp] = ssb_init_receiver(rootDir)
%SSB_INIT_RECEIVER Inicializa USRP B210 y parametros NR SSB para dataset.
%
% Uso:
%   [rx, params, precomp] = ssb_init_receiver(rootDir)

    if nargin < 1 || isempty(rootDir)
        rootDir = fullfile(getenv("HOME"), "AlbertoDir", "DT_sensing_fusion");
    end

    exampleDir = fullfile(rootDir, ...
        "NRSSBCaptureUsingSDRExample-20260619T104542Z-3-001", ...
        "NRSSBCaptureUsingSDRExample");

    addpath(exampleDir);
    addpath(fullfile(rootDir, "src", "matlab"));

    params = struct();

    % -------------------------
    % SDR
    % -------------------------
    radioOptions = hSDRBase.getDeviceNameOptions;
    rx = hSDRReceiver(radioOptions(10));

    antennaOptions = getAntennaOptions(rx);
    rx.ChannelMapping = antennaOptions(1);
    rx.Gain = 70;

    % -------------------------
    % Celda / SSB
    % -------------------------
    syncRasterInfo = hSynchronizationRasterInfo.SynchronizationRasterFR1;
    band = "n78";
    params.band = band;
    params.GSCN = 7875;
    params.useCustomCenterFrequency = false;

    if params.useCustomCenterFrequency
        rx.CenterFrequency = 3541.44e6;
    else
        rx.CenterFrequency = hSynchronizationRasterInfo.gscn2frequency(params.GSCN);
    end

    scsOptions = hSynchronizationRasterInfo.getSCSOptions(rx.CenterFrequency);
    scs = scsOptions(1);

    params.scs = scs;
    params.scsNumeric = double(extract(scs, digitsPattern));
    params.nrbSSB = 20;
    params.demodRB = 30;
    params.nSlot = 0;

    ofdmInfo = nrOFDMInfo(params.nrbSSB, params.scsNumeric);
    rx.SampleRate = ofdmInfo.SampleRate;

    params.sampleRate = rx.SampleRate;
    params.centerFrequency = rx.CenterFrequency;
    params.ssbBlockPattern = hSynchronizationRasterInfo.getBlockPattern(scs, rx.CenterFrequency);
    params.searchBW = 0.75 * params.scsNumeric;
    params.displayFigure = false;

    params.framesPerCapture = 1;
    params.captureDuration = seconds((params.framesPerCapture + 1) * 10e-3);

    if rx.CenterFrequency <= 3e9
        params.L_max = 4;
    else
        params.L_max = 8;
    end

    % -------------------------
    % GPU
    % -------------------------
    precomp = struct();

    try
        precomp.gpu = gpuDevice;
        params.useGPU = true;
        fprintf("GPU detectada: %s\n", precomp.gpu.Name);
    catch
        precomp.gpu = [];
        params.useGPU = false;
        warning("No se ha detectado GPU. Se usara CPU, puede ir mas lento.");
    end

    % -------------------------
    % Precalculo SSS references
    % -------------------------
    precomp.sssRefAll = cell(3,1);

    for nid2 = 0:2
        sssMat = zeros(127, 336);
        for nid1 = 0:335
            ncellid_tmp = 3*nid1 + nid2;
            sssMat(:, nid1+1) = nrSSS(ncellid_tmp);
        end

        if params.useGPU
            precomp.sssRefAll{nid2+1} = gpuArray(sssMat);
        else
            precomp.sssRefAll{nid2+1} = sssMat;
        end
    end

    fprintf("\n=== SSB receiver initialized ===\n");
    fprintf("Band: %s\n", params.band);
    fprintf("GSCN: %d\n", params.GSCN);
    fprintf("Center frequency: %.3f MHz\n", rx.CenterFrequency/1e6);
    fprintf("SCS: %d kHz\n", params.scsNumeric);
    fprintf("Sample rate: %.3f Msps\n", rx.SampleRate/1e6);
    fprintf("L_max: %d\n", params.L_max);
    fprintf("Capture duration: %.1f ms\n", seconds(params.captureDuration)*1000);
end
