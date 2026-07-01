
%% Online rxGridSSB streamer to Python inference server
% Synchronous request-response mode:
%   1) MATLAB captures and extracts one valid rxGridSSB.
%   2) MATLAB sends it to Python.
%   3) Python infers and returns one line.
%   4) MATLAB only then captures the next SSB.
%
% This means:
%   - no queue,
%   - no accumulated delay,
%   - if Python is slower, physical SSBs are missed because MATLAB waits,
%     but stale buffered predictions are not produced.

clear;
clc;

%% ------------------------------------------------------------------------
% Paths
% -------------------------------------------------------------------------

scriptDir = fileparts(mfilename("fullpath"));
projectRoot = fileparts(fileparts(scriptDir));

projectMatlabPath = fullfile(projectRoot, "src", "matlab");

exampleRoot = fullfile( ...
    projectRoot, ...
    "NRSSBCaptureUsingSDRExample-20260619T104542Z-3-001", ...
    "NRSSBCaptureUsingSDRExample");

addpath(genpath(projectMatlabPath));
addpath(genpath(exampleRoot));

fprintf("Project root: %s\n", projectRoot);
fprintf("Added project MATLAB path: %s\n", projectMatlabPath);
fprintf("Added NR SSB example path: %s\n", exampleRoot);
fprintf("hSDRBase location: %s\n", which("hSDRBase"));
fprintf("hSDRReceiver location: %s\n", which("hSDRReceiver"));
fprintf("mySSBurstFrequencyCorrectFast location: %s\n", which("mySSBurstFrequencyCorrectFast"));

%% ------------------------------------------------------------------------
% Configuration
% -------------------------------------------------------------------------

cfg = struct();

% TCP server
cfg.ServerHost = getenvDefault("PYTHON_INFERENCE_HOST", "127.0.0.1");
cfg.ServerPort = str2double(getenvDefault("PYTHON_INFERENCE_PORT", "5055"));

% Send every valid SSB. No intentional downsampling.
cfg.SendEveryN = str2double(getenvDefault("SEND_EVERY_N", "1"));

% Infinite by default. Set MAX_VALID_SSB=100 for test.
cfg.MaxValidSSB = str2double(getenvDefault("MAX_VALID_SSB", "Inf"));

% Warning threshold for MATLAB end-to-end iteration.
cfg.WarnTotalSeconds = str2double(getenvDefault("WARN_TOTAL_SECONDS", "0.200"));

% Warmup
cfg.NumWarmupCaptures = str2double(getenvDefault("WARMUP_CAPTURES", "30"));
cfg.MinValidWarmupCaptures = 10;

% Frequency correction
cfg.FrequencyCorrectionSign = str2double(getenvDefault("FREQ_CORRECTION_SIGN", "-1"));

% Periodic frequency resync. 0 disables it.
cfg.ResyncEveryNCaptures = str2double(getenvDefault("RESYNC_EVERY_N", "2000"));
cfg.ResyncCaptures = str2double(getenvDefault("RESYNC_CAPTURES", "8"));

% SDR selection
cfg.RadioOptionIndex = 10;
cfg.AntennaOptionIndex = 1;
cfg.RadioGain = 70;

% NR cell parameters
cfg.Band = "n78";
cfg.GSCN = 7875;
cfg.UseCustomCenterFrequency = false;
cfg.CustomCenterFrequencyHz = 3541.44e6;

% Grid parameters
cfg.NRBSSB = 20;
cfg.DemodRB = 30;
cfg.NSlot = 0;

% Capture duration: 20 ms
cfg.FramesPerCapture = 1;
cfg.CaptureDurationSeconds = (cfg.FramesPerCapture + 1) * 10e-3;

% Validated SSB region inside dataSSB/rxGridSave:
% MATLAB indexing:
%   rows 61:300
%   cols 2:5
cfg.SSBRows = 61:300;
cfg.SSBCols = 2:5;

cfg.ProgressEveryValid = 50;

%% ------------------------------------------------------------------------
% SDR setup
% -------------------------------------------------------------------------

radioOptions = hSDRBase.getDeviceNameOptions;
rx = hSDRReceiver(radioOptions(cfg.RadioOptionIndex));

cleanupObj = onCleanup(@() safeCleanup(rx)); %#ok<NASGU>

antennaOptions = getAntennaOptions(rx);
rx.ChannelMapping = antennaOptions(cfg.AntennaOptionIndex);
rx.Gain = cfg.RadioGain;

syncRasterInfo = hSynchronizationRasterInfo.SynchronizationRasterFR1;
bandRasterInfo = syncRasterInfo.(char(cfg.Band)); %#ok<NASGU>

if cfg.UseCustomCenterFrequency
    rx.CenterFrequency = cfg.CustomCenterFrequencyHz;
else
    rx.CenterFrequency = hSynchronizationRasterInfo.gscn2frequency(cfg.GSCN);
end

scsOptions = hSynchronizationRasterInfo.getSCSOptions(rx.CenterFrequency);
cfg.SCS = scsOptions(1);
cfg.SCSNumeric = double(extract(cfg.SCS, digitsPattern));

ofdmInfo = nrOFDMInfo(cfg.NRBSSB, cfg.SCSNumeric);
rx.SampleRate = ofdmInfo.SampleRate;

cfg.SampleRate = rx.SampleRate;
cfg.CenterFrequency = rx.CenterFrequency;
cfg.SSBBlockPattern = hSynchronizationRasterInfo.getBlockPattern(cfg.SCS, rx.CenterFrequency);
cfg.SearchBW = 0.75 * cfg.SCSNumeric;
cfg.DisplayFigure = false;

captureDuration = seconds(cfg.CaptureDurationSeconds);

fprintf("\n=== Online rxGridSSB streamer setup ===\n");
fprintf("Python server:          %s:%d\n", cfg.ServerHost, cfg.ServerPort);
fprintf("Send every N valid SSB: %d\n", cfg.SendEveryN);
fprintf("Max valid SSB:          %s\n", string(cfg.MaxValidSSB));
fprintf("Warning total time:     %.3f s\n", cfg.WarnTotalSeconds);
fprintf("Center frequency:       %.3f MHz\n", cfg.CenterFrequency / 1e6);
fprintf("Sample rate:            %.3f Msps\n", cfg.SampleRate / 1e6);
fprintf("SCS:                    %.0f kHz\n", cfg.SCSNumeric);
fprintf("Capture duration:       %.3f ms\n", cfg.CaptureDurationSeconds * 1000);
fprintf("rxGridSSB rows:         %d:%d\n", cfg.SSBRows(1), cfg.SSBRows(end));
fprintf("rxGridSSB cols:         %d:%d\n", cfg.SSBCols(1), cfg.SSBCols(end));
fprintf("Resync every N:         %d\n", cfg.ResyncEveryNCaptures);

%% ------------------------------------------------------------------------
% Connect to Python
% -------------------------------------------------------------------------

fprintf("\nConnecting to Python inference server...\n");

client = tcpclient(cfg.ServerHost, cfg.ServerPort, "Timeout", 10);

fprintf("Connected.\n");

%% ------------------------------------------------------------------------
% Warmup calibration
% -------------------------------------------------------------------------

fprintf("\n=== Warmup frequency/NID2 calibration ===\n");

syncState = calibrateFixedFrequency(rx, captureDuration, cfg, cfg.NumWarmupCaptures);

fprintf("\n=== Initial sync state ===\n");
fprintf("Fixed frequency offset: %.2f Hz\n", syncState.FrequencyOffsetHz);
fprintf("Fixed NID2:             %d\n", syncState.NID2);
fprintf("Valid warmup captures:  %d\n", syncState.ValidWarmupCaptures);

if syncState.ValidWarmupCaptures < cfg.MinValidWarmupCaptures
    error("Not enough valid warmup captures. Check signal or increase WARMUP_CAPTURES.");
end

%% ------------------------------------------------------------------------
% Main loop
% -------------------------------------------------------------------------

fprintf("\n=== Starting online stream ===\n");
fprintf("Synchronous mode: send -> wait result -> next capture.\n");
fprintf("Press Ctrl+C to stop.\n\n");

validCount = 0;
sentCount = 0;
failedCount = 0;

tExperiment = tic;

while validCount < cfg.MaxValidSSB

    tLoop = tic;

    try
        % Capture 20 ms IQ
        tCapture = tic;
        waveform = capture(rx, captureDuration);
        captureTime = toc(tCapture);

        % Process one waveform and extract rxGridSSB
        tProcess = tic;
        result = processOneWaveformToRxGridSSB(waveform, cfg, syncState);
        processTime = toc(tProcess);

        if ~result.Success
            failedCount = failedCount + 1;
            fprintf("Failed capture: %s\n", result.ErrorMessage);
            continue;
        end

        validCount = validCount + 1;

        % Optional periodic frequency resync.
        if cfg.ResyncEveryNCaptures > 0 && validCount > 1 && mod(validCount - 1, cfg.ResyncEveryNCaptures) == 0
            fprintf("\nResync at valid SSB %d...\n", validCount);

            newSyncState = calibrateFixedFrequency(rx, captureDuration, cfg, cfg.ResyncCaptures);

            if newSyncState.ValidWarmupCaptures >= max(3, floor(cfg.ResyncCaptures / 2))
                syncState = newSyncState;

                fprintf("Updated frequency offset: %.2f Hz | NID2=%d | valid=%d\n", ...
                    syncState.FrequencyOffsetHz, ...
                    syncState.NID2, ...
                    syncState.ValidWarmupCaptures);
            else
                fprintf("Resync ignored because only %d/%d captures were valid.\n", ...
                    newSyncState.ValidWarmupCaptures, cfg.ResyncCaptures);
            end
        end

        % Send every valid SSB by default.
        if mod(validCount, cfg.SendEveryN) == 0
            tSend = tic;
            responseLine = sendRxGridSSBAndWaitResult(client, result.RxGridSSB);
            sendWaitTime = toc(tSend);

            sentCount = sentCount + 1;

            totalLoopTime = toc(tLoop);

            warnText = "";
            if totalLoopTime > cfg.WarnTotalSeconds
                warnText = "  WARNING > 200 ms";
            end

            fprintf("MATLAB sent=%6d | valid=%6d | failed=%4d | capture=%.3f ms | process=%.3f ms | waitPython=%.3f ms | total=%.3f ms%s\n", ...
                sentCount, ...
                validCount, ...
                failedCount, ...
                captureTime * 1000, ...
                processTime * 1000, ...
                sendWaitTime * 1000, ...
                totalLoopTime * 1000, ...
                warnText);

            fprintf("Python: %s\n", responseLine);
        end

        if mod(validCount, cfg.ProgressEveryValid) == 0
            elapsed = toc(tExperiment);

            fprintf("Progress: valid=%d | sent=%d | failed=%d | valid rate=%.2f SSB/s | sent rate=%.2f/s\n", ...
                validCount, ...
                sentCount, ...
                failedCount, ...
                validCount / elapsed, ...
                sentCount / elapsed);
        end

    catch ME
        failedCount = failedCount + 1;
        fprintf("Loop error: %s\n", ME.message);
    end
end

fprintf("\nFinished online stream.\n");
fprintf("Valid SSB: %d\n", validCount);
fprintf("Sent:      %d\n", sentCount);
fprintf("Failed:    %d\n", failedCount);

release(rx);

%% ------------------------------------------------------------------------
% Local functions
% -------------------------------------------------------------------------

function value = getenvDefault(name, defaultValue)
    raw = getenv(char(name));

    if isempty(raw)
        value = defaultValue;
    else
        value = raw;
    end
end


function syncState = calibrateFixedFrequency(rx, captureDuration, cfg, numCaptures)
    freqOffsets = nan(numCaptures, 1);
    NID2Values = nan(numCaptures, 1);
    timingOffsets = nan(numCaptures, 1);
    validMask = false(numCaptures, 1);

    for k = 1:numCaptures
        try
            waveform = capture(rx, captureDuration);

            [correctedWaveform, freqOffset, NID2] = mySSBurstFrequencyCorrectFast( ...
                waveform, ...
                cfg.SSBBlockPattern, ...
                cfg.SampleRate, ...
                cfg.SearchBW, ...
                cfg.DisplayFigure);

            timingOffset = estimateTimingOffset(correctedWaveform, NID2, cfg);

            freqOffsets(k) = freqOffset;
            NID2Values(k) = NID2;
            timingOffsets(k) = timingOffset;
            validMask(k) = true;

            fprintf("Warmup %3d/%3d | freqOffset=%9.2f Hz | NID2=%d | timing=%d samples\n", ...
                k, numCaptures, freqOffset, NID2, timingOffset);

        catch ME
            fprintf("Warmup %3d/%3d failed: %s\n", k, numCaptures, ME.message);
        end
    end

    validFreq = freqOffsets(validMask);
    validNID2 = NID2Values(validMask);
    validTiming = timingOffsets(validMask);

    syncState = struct();

    if isempty(validFreq)
        syncState.FrequencyOffsetHz = NaN;
        syncState.NID2 = NaN;
        syncState.ValidWarmupCaptures = 0;
        syncState.FrequencyOffsetsWarmup = freqOffsets;
        syncState.NID2Warmup = NID2Values;
        syncState.TimingOffsetsWarmup = timingOffsets;
        syncState.ValidWarmupMask = validMask;
        return;
    end

    syncState.FrequencyOffsetHz = median(validFreq, "omitnan");
    syncState.NID2 = mode(validNID2);
    syncState.TimingOffsetMedianWarmup = round(median(validTiming, "omitnan"));
    syncState.ValidWarmupCaptures = sum(validMask);

    syncState.FrequencyOffsetsWarmup = freqOffsets;
    syncState.NID2Warmup = NID2Values;
    syncState.TimingOffsetsWarmup = timingOffsets;
    syncState.ValidWarmupMask = validMask;
end


function timingOffset = estimateTimingOffset(correctedWaveform, NID2, cfg)
    refGridTim = zeros([cfg.NRBSSB * 12, 2]);
    refGridTim(nrPSSIndices, 2) = nrPSS(NID2);

    timingOffset = nrTimingEstimate( ...
        correctedWaveform, ...
        cfg.NRBSSB, ...
        cfg.SCSNumeric, ...
        cfg.NSlot, ...
        refGridTim, ...
        SampleRate = cfg.SampleRate);

    if isempty(timingOffset) || timingOffset < 0 || timingOffset >= size(correctedWaveform, 1)
        error("Invalid timingOffset: %s", mat2str(timingOffset));
    end
end


function result = processOneWaveformToRxGridSSB(waveform, cfg, syncState)
    result = struct();

    result.Success = false;
    result.ErrorMessage = "";
    result.RxGridSSB = [];
    result.TimingOffsetUsed = NaN;

    try
        if isnan(syncState.FrequencyOffsetHz) || isnan(syncState.NID2)
            error("Invalid sync state.");
        end

        correctedWaveform = applyFrequencyCorrection( ...
            waveform, ...
            syncState.FrequencyOffsetHz, ...
            cfg.SampleRate, ...
            cfg.FrequencyCorrectionSign);

        timingOffset = estimateTimingOffset(correctedWaveform, syncState.NID2, cfg);
        result.TimingOffsetUsed = timingOffset;

        correctedWaveformAligned = correctedWaveform(1 + timingOffset:end, :);

        rxGridSave = nrOFDMDemodulate( ...
            correctedWaveformAligned, ...
            cfg.DemodRB, ...
            cfg.SCSNumeric, ...
            cfg.NSlot, ...
            SampleRate = cfg.SampleRate);

        if size(rxGridSave, 1) < cfg.SSBRows(end)
            error("Not enough subcarriers in rxGridSave.");
        end

        if size(rxGridSave, 2) < cfg.SSBCols(end)
            error("Not enough OFDM symbols in rxGridSave: %d", size(rxGridSave, 2));
        end

        rxGridSSB = rxGridSave(cfg.SSBRows, cfg.SSBCols, 1);

        result.RxGridSSB = single(rxGridSSB);
        result.Success = true;

    catch ME
        result.Success = false;
        result.ErrorMessage = string(ME.message);
    end
end


function y = applyFrequencyCorrection(x, freqOffsetHz, sampleRate, correctionSign)
    n = (0:size(x, 1)-1).';
    rot = exp(1j * correctionSign * 2 * pi * freqOffsetHz * n / sampleRate);
    y = x .* rot;
end


function responseLine = sendRxGridSSBAndWaitResult(client, rxGridSSB)
    payload = zeros(2, 240, 4, "single");

    payload(1, :, :) = real(rxGridSSB);
    payload(2, :, :) = imag(rxGridSSB);

    payloadBytes = typecast(payload(:), "uint8");
    headerBytes = typecast(uint32(numel(payloadBytes)), "uint8");

    packet = [headerBytes(:); payloadBytes(:)];

    write(client, packet, "uint8");

    responseLine = readLineFromTcp(client);
end


function line = readLineFromTcp(client)
    bytes = uint8([]);

    while true
        b = read(client, 1, "uint8");

        if isempty(b)
            pause(0.001);
            continue;
        end

        if b == uint8(10)
            break;
        end

        bytes(end + 1) = b; %#ok<AGROW>
    end

    line = string(char(bytes));
end


function safeCleanup(rx)
    try
        release(rx);
    catch
    end
end