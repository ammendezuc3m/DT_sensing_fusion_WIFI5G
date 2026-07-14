function [decBits,decParams,searchOffsetOut,res] = recoverDSSSBits(rx,searchOffsetIn)
%recoverDSSSBits Performs non-HT DSSS signal recovery
%   [DECBITS,DECPARAMS,SEARCHOFFSETOUT,RES] =
%   recoverDSSSBits(CAPTUREDDATA,SEARCHOFFSETIN) detects a DSSS packet and
%   performs analysis of the non-HT DSSS preamble, SIGNAL, data fields.
%
%   DECBITS is a vector containing the decoded bits in a non-HT packet.
%
%   DECPARAMS is a structure containing the decoded signal parameters.
%
%   SEARCHOFFSETOUT is the offset from the start of RX in samples to the
%   next point from which to search for a packet.
%
%   RES is a structure containing signal analysis.
%
%   RX is the received time-domain waveform. It is a Ns-by-Nr matrix of
%   real or complex values, where Ns represents the number of time-domain
%   samples in the waveform, and Nr represents the number of receive
%   antennas.
%
%   SEARCHOFFSETIN is the offset from the start of RX in samples to the
%   next point from which to search for a packet.

%   Copyright 2024 The MathWorks, Inc.

    decBits = [];

    % Structure containing the decoded information
    decParams = struct;
    decParams.modulation = nan;
    decParams.dataRate = nan;

    % Structure containing signal analysis
    res = struct;
    res.PacketOffset = nan;

    % DSSS Parameters
    % 802.11b/g (DSSS) configuration
    dsssCfg = wlanNonHTConfig('Modulation', 'DSSS', ...
                              'Preamble', 'Long');
    info = wlan.internal.dsssInfo(dsssCfg);
    preamble = wlan.internal.wlanDSSSPreamble(dsssCfg);
    Barker = [1 -1 1 1 -1 1 1 1 -1 -1 -1].';
    barkerSeqLen = length(Barker);

    % Before Barker spreading
    syncBitsLen = 128;
    sfdBitsLen = 16;
    preambleBitsLen = syncBitsLen+sfdBitsLen;
    headerBitsLen = 48;
    plcpBitsLen = preambleBitsLen+headerBitsLen;

    % After Barker spreading
    syncLen = syncBitsLen*barkerSeqLen;
    sfdLen = sfdBitsLen*barkerSeqLen;
    preambleLen = preambleBitsLen*barkerSeqLen;
    plcpLen = plcpBitsLen*barkerSeqLen;
    init = double(info.ScramblerInitialization);

    % Define preamble detector object
    peakCorrDetect = comm.PreambleDetector(Detections="First");
    peakCorrDetect.Preamble = preamble(syncLen+(1:sfdLen));

    % CRC detector configuration
    crcCfg = crcConfig('Polynomial',[16 12 5 0],'InitialConditions',ones(1,16),'DirectMethod',true);

    % Extract waveform to search
    searchOffsetOut = searchOffsetIn + length(rx);
    rx = rx(searchOffsetIn+1:end);
    rx = rx - mean(rx);

    if length(rx) >= plcpLen && nnz(rx)~=0

        for ii = 1:preambleLen:length(rx)-(preambleLen+sfdLen-2)

            % Search for preamble within (preamble length + SFD length -1) of burst
            preambleSearchBurst = rx(ii:(ii+preambleLen+sfdLen-2));

            % Detect peak of correlation with SFD sequence
            [~, dtMt] = peakCorrDetect(preambleSearchBurst);
            release(peakCorrDetect);
            [~,peakCorrelationIndex] = max(dtMt);
            packetOffset = peakCorrelationIndex-preambleLen+ii-1;
            res.PacketOffset = packetOffset;

            if (packetOffset<0) || (peakCorrelationIndex<sfdLen)
                % Packet offset should be positive and the correlation peak
                % index should be minimum SFD length
                searchOffsetOut = searchOffsetIn + packetOffset + preambleLen;
                continue;
            end

            syncData = rx(packetOffset+1:end);

            % Scale rx by the power of preamble
            rxPreamble = syncData(1:preambleLen);
            rxPreamblePower = mean(rxPreamble(:).*conj(rxPreamble(:)));
            syncData = syncData./sqrt(rxPreamblePower);

            % Preamble decoding
            if length(syncData) < plcpLen
                % Exit the function if there are less than PLCP length of
                % samples
                searchOffsetOut = searchOffsetIn + packetOffset + preambleLen;
                return;
            end

            PSHSamples = syncData(1:plcpLen).';
            PSHSamplesMatrix = (reshape(PSHSamples,barkerSeqLen,[])).';
            PSHSyms = (PSHSamplesMatrix*Barker)/barkerSeqLen;
            PSHBits = dpskdemod(PSHSyms,2,0,'gray');
            [PSHDescramBits, init] = dsssDescramble(PSHBits,init);

            headerBits = PSHDescramBits(144+1:end).';
            CRC_input = headerBits;

            % Invert CRC check bits before giving to detector
            CRC_input = [CRC_input(1:end-16) ~CRC_input(end-16+1:end)];
            [~,crcError] = crcDetect(CRC_input.',crcCfg);

            if crcError
                % Skip the iteration if there is CRC error.
                searchOffsetOut = searchOffsetIn + packetOffset + preambleLen;
                decParams.failCheck = 1;
                continue;
            end

            % Read Header
            modulationIdx = bit2int(fliplr(headerBits(1:8)).',8);
            lengthMicroSec = bit2int(fliplr(headerBits(17:32)).',16);
            switch modulationIdx
              case 10 % DBPSK
                PSDULengthBits = lengthMicroSec;
                modulation = 'DBPSK';
                dataRate = '1Mbps';
                endIdx = plcpLen+PSDULengthBits*1*barkerSeqLen;
                if size(syncData,1)>=endIdx
                    dataSamples = syncData(plcpLen+1:endIdx).';
                    dataSamplesMatrix = reshape(dataSamples,barkerSeqLen,[]).';
                    dataSyms = (dataSamplesMatrix*Barker)/barkerSeqLen;
                    decodedBits = dpskdemod(conj(PSHSyms(end))*dataSyms,2,0,'gray').';
                    decBits = dsssDescramble(decodedBits,init);
                    searchOffsetOut = searchOffsetIn + packetOffset + (plcpLen+length(dataSamples));
                    decParams = assignParams(modulation,dataRate,PSDULengthBits);
                    return; % Exit the function and proceed with MAC decoding
                end
              case 20 % DQPSK
                PSDULengthBits = lengthMicroSec*2;
                modulation = 'DQPSK';
                dataRate = '2Mbps';
                endIdx = plcpLen+PSDULengthBits*(1/2)*barkerSeqLen;
                if size(syncData,1) >= endIdx
                    dataSamples = syncData(plcpLen+1:endIdx).';
                    dataSamplesMatrix = reshape(dataSamples,barkerSeqLen,[]).';
                    dataSyms = (dataSamplesMatrix*Barker)/barkerSeqLen;
                    dataCW = dpskdemod(conj(PSHSyms(end))*dataSyms,4,0,'gray');
                    decodedBits = reshape(int2bit(dataCW,2).',[],1);
                    decBits = dsssDescramble(decodedBits,init);
                    searchOffsetOut = searchOffsetIn + packetOffset + (plcpLen+length(dataSamples));
                    decParams = assignParams(modulation,dataRate,PSDULengthBits);
                    return; % Exit the function and proceed with MAC decoding
                end
              case 55 % CCK 5.5
                PSDULengthBits = floor(lengthMicroSec*5.5/8)*8;
                endIdx = (plcpLen+PSDULengthBits*(1/4)*8);
                modulation = 'CCK5.5';
                dataRate = '5.5Mbps';
                if size(syncData,1) >= endIdx
                    dataSamples = syncData(plcpLen+1:endIdx).';
                    decodedBits = dsssCCKDemodulate(dataSamples, dataRate, PSHSyms(end));
                    decBits = dsssDescramble(decodedBits,init);
                    searchOffsetOut = searchOffsetIn + packetOffset + (plcpLen+length(dataSamples));
                    decParams = assignParams(modulation,dataRate,PSDULengthBits);
                    return; % Exit the function and proceed with MAC decoding
                end
              otherwise % CCK 11
                if headerBits(16) == 1
                    PSDULengthBits = (floor(lengthMicroSec*11/8)-1)*8;
                else
                    PSDULengthBits = floor(lengthMicroSec*11/8)*8;
                end
                endIdx = plcpLen+PSDULengthBits*(1/8)*8;
                modulation = 'CCK11';
                dataRate = '11Mbps';
                if size(syncData,1) >= endIdx
                    dataSamples = syncData(plcpLen+1:endIdx).';
                    decodedBits = dsssCCKDemodulate(dataSamples, dataRate, PSHSyms(end));
                    decBits = dsssDescramble(decodedBits,init);
                    searchOffsetOut = searchOffsetIn + packetOffset + (plcpLen+length(dataSamples));
                    decParams = assignParams(modulation,dataRate,PSDULengthBits);
                    return; % Exit the function and proceed with MAC decoding
                end
            end
            searchOffsetOut =  searchOffsetIn + packetOffset + plcpLen;
        end
    end
end


function decParams = assignParams(modulation,dataRate,PSDULengthBits)

    decParams.modulation = modulation;
    decParams.dataRate = dataRate;
    decParams.PSDULength = PSDULengthBits/8; % in octets
    decParams.failCheck = 0;

end
