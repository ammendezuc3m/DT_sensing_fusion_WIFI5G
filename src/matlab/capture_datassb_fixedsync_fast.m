%% Fast fixed-sync dataSSB capture
% Goal:
%   Capture many aligned dataSSB snapshots as fast as possible.
%
% Output:
%   dataSSB: 360 x 6 x N complex single
%
% Strategy:
%   1) Add project and NR SSB example helper paths.
%   2) Configure SDR.
%   3) Run a short warmup calibration:
%        - estimate frequency offset
%        - estimate NID2
%        - estimate timing offset
%   4) Main loop:
%        - capture 20 ms
%        - apply fixed frequency correction
%        - apply fixed timing offset
%        - demodulate 30 RB
%        - save first 6 OFDM symbols as dataSSB
%
% This script does NOT:
%   - plot
%   - decode PBCH/BCH
%   - scan DMRS
%   - estimate hSSB
%   - use backgroundPool

clear;
clc;

%% ------------------------------------------------------------------------
% Add local helper paths
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
% User configuration
% -------------------------------------------------------------------------

cfg = struct();

cfg.TotalCaptures = 1000;

% Warmup calibration
cfg.NumWarmupCaptures = 30;
cfg.MinValidWarmupCaptures = 10;

% If the fixed correction sign is wrong, change this from -1 to +1.
% In most cases, -1 is correct because we compensate the estimated offset.
cfg.FrequencyCorrectionSign = -1;

% Optional periodic resync.
% 0 means never resync after warmup.
% Example: 200 means refresh fixed offset/timing every 200 captures.
cfg.ResyncEveryNCaptures = 0;
cfg.ResyncCaptures = 8;

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
cfg.NRBSSB = 20;             % 20 RB = 240 subcarriers
cfg.DemodRB = 30;            % 30 RB = 360 subcarriers
cfg.NumSymbolsToSave = 6;    % dataSSB = 360 x 6 x N
cfg.NSlot = 0;

% Capture duration: 20 ms
cfg.FramesPerCapture = 1;
cfg.CaptureDurationSeconds = (cfg.FramesPerCapture + 1) * 10e-3;

% Rate metrics
cfg.ProgressEvery = 50;
cfg.IgnoreFirstNForSteadyRate = 5;

% Output
timestamp = datestr(now, "yyyymmdd_HHMMSS");
cfg.OutputDir = fullfile(projectRoot, "data", "fast_datassb");
cfg.OutputFile = fullfile(cfg.OutputDir, "datassb_fixedsync_fast_" + string(timestamp) + ".mat");

if ~exist(cfg.OutputDir, "dir")
    mkdir(cfg.OutputDir);
end

%% ------------------------------------------------------------------------
% SDR setup
% -------------------------------------------------------------------------

radioOptions = hSDRBase.getDeviceNameOptions;
rx = hSDRReceiver(radioOptions(cfg.RadioOptionIndex));

cleanupObj = onCleanup(@() safeRelease(rx));

antennaOptions = getAntennaOptions(rx);
rx.ChannelMapping = antennaOptions(cfg.AntennaOptionIndex);
rx.Gain = cfg.RadioGain;

syncRasterInfo = hSynchronizationRasterInfo.SynchronizationRasterFR1;
bandRasterInfo = syncRasterInfo.(cfg.Band); %#ok<NASGU>

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

fprintf("\n=== Fixed-sync fast dataSSB capture setup ===\n");
fprintf("Center frequency: %.3f MHz\n", cfg.CenterFrequency / 1e6);
fprintf("Sample rate: %.3f Msps\n", cfg.SampleRate / 1e6);
fprintf("SCS: %.0f kHz\n", cfg.SCSNumeric);
fprintf("Capture duration: %.3f ms\n", cfg.CaptureDurationSeconds * 1000);
fprintf("Total captures: %d\n", cfg.TotalCaptures);
fprintf("Warmup captures: %d\n", cfg.NumWarmupCaptures);
fprintf("Output file: %s\n", cfg.OutputFile);

%% ------------------------------------------------------------------------
% Warmup calibration
% -------------------------------------------------------------------------

fprintf("\n=== Warmup sync calibration ===\n");

syncState = calibrateFixedSync(rx, captureDuration, cfg, cfg.NumWarmupCaptures);

fprintf("\n=== Fixed sync state ===\n");
fprintf("Fixed frequency offset: %.2f Hz\n", syncState.FrequencyOffsetHz);
fprintf("Fixed NID2: %d\n", syncState.NID2);
fprintf("Fixed timing offset: %d samples\n", syncState.TimingOffsetSamples);
fprintf("Valid warmup captures: %d\n", syncState.ValidWarmupCaptures);

if syncState.ValidWarmupCaptures < cfg.MinValidWarmupCaptures
    error("Not enough valid warmup captures. Check signal or increase cfg.NumWarmupCaptures.");
end

%% ------------------------------------------------------------------------
% Preallocate output arrays
% -------------------------------------------------------------------------

numSC = cfg.DemodRB * 12;

dataSSB = complex(zeros(numSC, cfg.NumSymbolsToSave, cfg.TotalCaptures, "single"));

validMask = false(cfg.TotalCaptures, 1);
errorMessages = strings(cfg.TotalCaptures, 1);

freqOffsets = nan(cfg.TotalCaptures, 1);
NID2Log = nan(cfg.TotalCaptures, 1);
timingOffsets = nan(cfg.TotalCaptures, 1);
nSymbolsGridSave = nan(cfg.TotalCaptures, 1);

tWallIter = nan(cfg.TotalCaptures, 1);
tCapture = nan(cfg.TotalCaptures, 1);
tProcessTotal = nan(cfg.TotalCaptures, 1);
tFreqApply = nan(cfg.TotalCaptures, 1);
tAlign = nan(cfg.TotalCaptures, 1);
tDemodSave = nan(cfg.TotalCaptures, 1);
tResync = nan(cfg.TotalCaptures, 1);

%% ------------------------------------------------------------------------
% Main capture loop
% -------------------------------------------------------------------------

fprintf("\n=== Starting fixed-sync fast capture ===\n");

tExperiment = tic;

for captureIdx = 1:cfg.TotalCaptures

    tLoop = tic;

    % Capture
    t0 = tic;
    waveform = capture(rx, captureDuration);
    tCapture(captureIdx) = toc(t0);

    % Optional periodic resync
    if cfg.ResyncEveryNCaptures > 0 && captureIdx > 1 && mod(captureIdx - 1, cfg.ResyncEveryNCaptures) == 0
        t0 = tic;
        fprintf("\nResync at capture %d...\n", captureIdx);
        syncState = calibrateFixedSync(rx, captureDuration, cfg, cfg.ResyncCaptures);
        tResync(captureIdx) = toc(t0);

        fprintf("Updated fixed frequency offset: %.2f Hz | NID2=%d | timing=%d samples | valid=%d\n", ...
            syncState.FrequencyOffsetHz, ...
            syncState.NID2, ...
            syncState.TimingOffsetSamples, ...
            syncState.ValidWarmupCaptures);
    end

    % Process using fixed sync
    result = processOneWaveformFixedSync(waveform, cfg, syncState);

    if result.Success
        dataSSB(:, :, captureIdx) = result.DataSSB;
        validMask(captureIdx) = true;

        freqOffsets(captureIdx) = syncState.FrequencyOffsetHz;
        NID2Log(captureIdx) = syncState.NID2;
        timingOffsets(captureIdx) = syncState.TimingOffsetSamples;
        nSymbolsGridSave(captureIdx) = result.NSymbolsGridSave;

        tProcessTotal(captureIdx) = result.TProcessTotal;
        tFreqApply(captureIdx) = result.TFreqApply;
        tAlign(captureIdx) = result.TAlign;
        tDemodSave(captureIdx) = result.TDemodSave;
    else
        validMask(captureIdx) = false;
        errorMessages(captureIdx) = result.ErrorMessage;

        tProcessTotal(captureIdx) = result.TProcessTotal;
        tFreqApply(captureIdx) = result.TFreqApply;
        tAlign(captureIdx) = result.TAlign;
        tDemodSave(captureIdx) = result.TDemodSave;
    end

    tWallIter(captureIdx) = toc(tLoop);

    if mod(captureIdx, cfg.ProgressEvery) == 0
        elapsedNow = toc(tExperiment);
        validNow = sum(validMask);
        fprintf("Completed %4d/%4d | valid=%4d | elapsed=%.2f s | valid rate=%.2f dataSSB/s\n", ...
            captureIdx, cfg.TotalCaptures, validNow, elapsedNow, validNow / elapsedNow);
    end
end

elapsedExperiment = toc(tExperiment);

%% ------------------------------------------------------------------------
% Summary
% -------------------------------------------------------------------------

validCount = sum(validMask);
failedCount = cfg.TotalCaptures - validCount;

steadyIdx = validMask;
steadyIdx(1:min(cfg.IgnoreFirstNForSteadyRate, cfg.TotalCaptures)) = false;

steadyTotalTime = sum(tWallIter(steadyIdx), "omitnan");
steadyValidCount = sum(steadyIdx);

summary = struct();
summary.TotalCaptures = cfg.TotalCaptures;
summary.ValidDataSSB = validCount;
summary.FailedCaptures = failedCount;
summary.ElapsedSeconds = elapsedExperiment;
summary.ValidDataSSBPerSecondTotal = validCount / elapsedExperiment;
summary.SuccessRatePercent = 100 * validCount / cfg.TotalCaptures;
summary.SteadyValidDataSSB = steadyValidCount;
summary.SteadySecondsFromWallIter = steadyTotalTime;
summary.SteadyValidDataSSBPerSecond = steadyValidCount / steadyTotalTime;

summary.MeanWallIterSeconds = mean(tWallIter(validMask), "omitnan");
summary.MedianWallIterSeconds = median(tWallIter(validMask), "omitnan");
summary.MeanCaptureTimeSeconds = mean(tCapture(validMask), "omitnan");
summary.MedianCaptureTimeSeconds = median(tCapture(validMask), "omitnan");
summary.MeanProcessTimeSeconds = mean(tProcessTotal(validMask), "omitnan");
summary.MedianProcessTimeSeconds = median(tProcessTotal(validMask), "omitnan");

fprintf("\n=== Capture summary ===\n");
fprintf("Total captures:                 %d\n", summary.TotalCaptures);
fprintf("Valid dataSSB:                  %d\n", summary.ValidDataSSB);
fprintf("Failed captures:                %d\n", summary.FailedCaptures);
fprintf("Elapsed wall time:              %.3f s\n", summary.ElapsedSeconds);
fprintf("Valid dataSSB per second total: %.3f\n", summary.ValidDataSSBPerSecondTotal);
fprintf("Success rate:                   %.2f %%\n", summary.SuccessRatePercent);
fprintf("Steady valid dataSSB/s:         %.3f\n", summary.SteadyValidDataSSBPerSecond);
fprintf("Mean wall iter time:            %.5f s\n", summary.MeanWallIterSeconds);
fprintf("Median wall iter time:          %.5f s\n", summary.MedianWallIterSeconds);
fprintf("Mean capture time:              %.5f s\n", summary.MeanCaptureTimeSeconds);
fprintf("Median capture time:            %.5f s\n", summary.MedianCaptureTimeSeconds);
fprintf("Mean process time:              %.5f s\n", summary.MeanProcessTimeSeconds);
fprintf("Median process time:            %.5f s\n", summary.MedianProcessTimeSeconds);

fprintf("\n=== Timing summary, valid captures only ===\n");
printMetric("tWallIter", tWallIter(validMask));
printMetric("tCapture", tCapture(validMask));
printMetric("tProcessTotal", tProcessTotal(validMask));
printMetric("tFreqApply", tFreqApply(validMask));
printMetric("tAlign", tAlign(validMask));
printMetric("tDemodSave", tDemodSave(validMask));
printMetric("tResync", tResync(~isnan(tResync)));

%% ------------------------------------------------------------------------
% Save results
% -------------------------------------------------------------------------

save( ...
    cfg.OutputFile, ...
    "dataSSB", ...
    "validMask", ...
    "errorMessages", ...
    "freqOffsets", ...
    "NID2Log", ...
    "timingOffsets", ...
    "nSymbolsGridSave", ...
    "tWallIter", ...
    "tCapture", ...
    "tProcessTotal", ...
    "tFreqApply", ...
    "tAlign", ...
    "tDemodSave", ...
    "tResync", ...
    "cfg", ...
    "summary", ...
    "syncState", ...
    "-v7.3");

fprintf("\nSaved output file:\n%s\n", cfg.OutputFile);

release(rx);

%% ------------------------------------------------------------------------
% Local functions
% -------------------------------------------------------------------------

function syncState = calibrateFixedSync(rx, captureDuration, cfg, numCaptures)
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
        syncState.TimingOffsetSamples = NaN;
        syncState.ValidWarmupCaptures = 0;
        return;
    end

    syncState.FrequencyOffsetHz = median(validFreq, "omitnan");
    syncState.NID2 = mode(validNID2);
    syncState.TimingOffsetSamples = round(median(validTiming, "omitnan"));
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


function result = processOneWaveformFixedSync(waveform, cfg, syncState)
    result = struct();

    result.Success = false;
    result.ErrorMessage = "";
    result.DataSSB = [];
    result.NSymbolsGridSave = NaN;

    result.TProcessTotal = NaN;
    result.TFreqApply = NaN;
    result.TAlign = NaN;
    result.TDemodSave = NaN;

    tAll = tic;

    try
        if isnan(syncState.FrequencyOffsetHz) || isnan(syncState.TimingOffsetSamples)
            error("Invalid fixed sync state.");
        end

        %% Fixed frequency correction
        t0 = tic;
        correctedWaveform = applyFrequencyCorrection( ...
            waveform, ...
            syncState.FrequencyOffsetHz, ...
            cfg.SampleRate, ...
            cfg.FrequencyCorrectionSign);
        result.TFreqApply = toc(t0);

        %% Fixed timing alignment
        t0 = tic;
        timingOffset = syncState.TimingOffsetSamples;

        if timingOffset < 0 || timingOffset >= size(correctedWaveform, 1)
            error("Invalid fixed timingOffset: %d", timingOffset);
        end

        correctedWaveformAligned = correctedWaveform(1 + timingOffset:end, :);
        result.TAlign = toc(t0);

        %% OFDM demodulate 30 RB and save first 6 symbols
        t0 = tic;

        rxGridSave = nrOFDMDemodulate( ...
            correctedWaveformAligned, ...
            cfg.DemodRB, ...
            cfg.SCSNumeric, ...
            cfg.NSlot, ...
            SampleRate = cfg.SampleRate);

        nKeep = min(cfg.NumSymbolsToSave, size(rxGridSave, 2));

        if nKeep < 4
            error("Not enough OFDM symbols after demodulation: %d", nKeep);
        end

        tmp = complex(zeros(cfg.DemodRB * 12, cfg.NumSymbolsToSave, "single"));
        tmp(:, 1:nKeep) = single(rxGridSave(:, 1:nKeep, 1));

        result.TDemodSave = toc(t0);

        result.Success = true;
        result.DataSSB = tmp;
        result.NSymbolsGridSave = size(rxGridSave, 2);
        result.TProcessTotal = toc(tAll);

    catch ME
        result.Success = false;
        result.ErrorMessage = string(ME.message);
        result.TProcessTotal = toc(tAll);
    end
end


function y = applyFrequencyCorrection(x, freqOffsetHz, sampleRate, correctionSign)
    n = (0:size(x, 1)-1).';
    rot = exp(1j * correctionSign * 2 * pi * freqOffsetHz * n / sampleRate);
    y = x .* rot;
end


function printMetric(name, values)
    values = values(~isnan(values));

    if isempty(values)
        fprintf("%-18s | no valid values\n", name);
        return;
    end

    fprintf("%-18s | N=%4d | mean=%.5f | median=%.5f | p95=%.5f | min=%.5f | max=%.5f\n", ...
        name, ...
        numel(values), ...
        mean(values), ...
        median(values), ...
        prctile(values, 95), ...
        min(values), ...
        max(values));
end


function safeRelease(rx)
    try
        release(rx);
    catch
    end
end
