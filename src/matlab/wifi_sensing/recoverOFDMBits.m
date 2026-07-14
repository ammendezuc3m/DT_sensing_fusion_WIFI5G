function [decBits,decParams,searchOffset,res] = recoverOFDMBits(rx,searchOffset)
%recoverOFDMBits Performs non-HT OFDM signal recovery
%   [DECBITS,DECPARAMS,SEARCHOFFSET,RES] = recoverOFDMBits(RX,SEARCHOFFSET)
%   detects a OFDM packet and performs analysis of the non-HT preamble,
%   L-SIG, data fields.
%
%   DECBITS is a vector containing the decoded bits in a non-HT packet.
%
%   DECPARAMS is a structure containing the decoded signal parameters.
%
%   SEARCHOFFSET is the offset from the start of RX in samples to the
%   next point from which to search for a packet.
%
%   RES is a structure containing signal analysis.
%
%   RX is the received time-domain waveform. It is a Ns-by-Nr matrix of
%   real or complex values, where Ns represents the number of time-domain
%   samples in the waveform, and Nr represents the number of receive
%   antennas.

%   Copyright 2024 The MathWorks, Inc.

% recoverPreamble detects a packet and performs analysis of the non-HT preamble.
    decBits = [];

    decParams = struct;
    decParams.modulation = nan;
    decParams.codeRate = nan;
    decParams.MCS = nan;
    decParams.PSDULength = nan;
    decParams.failCheck = nan;

    cbw = "CBW20";
    cfg = wlanNonHTConfig(ChannelBandwidth=cbw);
    ind = wlanFieldIndices(cfg);
    sampleRate = 20e6;
    maxNonHTPacketTime = 5.484e-3;
    maxNonHTPacketSamples = maxNonHTPacketTime*sampleRate;

    [preambleStatus,res] = recoverPreamble(rx,cbw,searchOffset);

    if matches(preambleStatus,"No packet detected")
        searchOffset = length(rx);
        return;
    end

    % Retrieve synchronized data and scale it with LSTF power as done
    % in the recoverPreamble function.
    if maxNonHTPacketSamples <= (length(rx) - res.PacketOffset)
        endIdx = maxNonHTPacketSamples + res.PacketOffset;
    else
        endIdx = length(rx);
    end
    syncData = rx(res.PacketOffset+1:endIdx)./sqrt(res.LSTFPower);
    syncData = frequencyOffset(syncData,sampleRate,-res.CFOEstimate);

    % Need only 4 OFDM symbols (LSIG + 3 more symbols) following LLTF
    % for format detection
    fmtDetect = syncData(ind.LSIG(1):(ind.LSIG(2)+4e-6*sampleRate*3));

    [LSIGBits, failcheck] = wlanLSIGRecover(fmtDetect(1:4e-6*sampleRate*1), ...
                                            res.ChanEstNonHT,res.NoiseEstNonHT,cbw);

    decParams.failCheck = failcheck;

    if ~failcheck
        format = wlanFormatDetect(fmtDetect,res.ChanEstNonHT,res.NoiseEstNonHT,cbw);
        if matches(format,"Non-HT")

            % Extract MCS from first 3 bits of L-SIG.
            rate = double(bit2int(LSIGBits(1:3),3));
            if rate <= 1
                cfg.MCS = rate + 6;
            else
                cfg.MCS = mod(rate,6);
            end
            [modulation,coderate] = getRateInfo(cfg.MCS);
            decParams.modulation = modulation;
            decParams.coderate = coderate;
            decParams.MCS = cfg.MCS;

            % Determine PSDU length from L-SIG.
            cfg.PSDULength = double(bit2int(LSIGBits(6:17),12,0));
            decParams.PSDULength = cfg.PSDULength;
            ind.NonHTData = wlanFieldIndices(cfg,"NonHT-Data");

            if double(ind.NonHTData(2)-ind.NonHTData(1))> ...
                    length(syncData(ind.NonHTData(1):end))
                % Exit function as full packet not captured.
                searchOffset = length(rx);
                return;
            end

            nonHTData = syncData(ind.NonHTData(1):ind.NonHTData(2));
            decBits = wlanNonHTDataRecover(nonHTData,res.ChanEstNonHT, ...
                                           res.NoiseEstNonHT,cfg);
            % Shift packet search offset for next iteration of while loop.
            searchOffset = res.PacketOffset + double(ind.NonHTData(2));
        else
            % Packet is NOT non-HT; shift packet search offset by 10 OFDM symbols (minimum
            % packet length of non-HT) for next iteration of while loop.
            searchOffset = res.PacketOffset + 4e-6*sampleRate*10;
        end
    else
        % L-SIG recovery failed; shift packet search offset by 10 OFDM symbols (minimum
        % packet length of non-HT) for next iteration of while loop.
        searchOffset = res.PacketOffset + 4e-6*sampleRate*10;
    end
end

function [modulation,coderate] = getRateInfo(mcs)
% GETRATEINFO returns the modulation scheme as a character array and the
% code rate of a packet given a scalar integer representing the modulation
% coding scheme
    switch mcs
      case 0 % BPSK
        modulation = 'BPSK';
        coderate = '1/2';
      case 1 % BPSK
        modulation = 'BPSK';
        coderate = '3/4';
      case 2 % QPSK
        modulation = 'QPSK';
        coderate = '1/2';
      case 3 % QPSK
        modulation = 'QPSK';
        coderate = '3/4';
      case 4 % 16QAM
        modulation = '16QAM';
        coderate = '1/2';
      case 5 % 16QAM
        modulation = '16QAM';
        coderate = '3/4';
      case 6 % 64QAM
        modulation = '64QAM';
        coderate = '2/3';
      otherwise % 64QAM
        modulation = '64QAM';
        coderate = '3/4';
    end
end
