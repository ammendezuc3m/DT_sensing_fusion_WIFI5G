%% Dataset block capture for fast dataSSB sensing with SSB timestamps
% This script captures one dataset block for one label and stores dataSSB
% plus timestamp information for each captured SSB.
%
% Launch example:
%
%   CAPTURE_LABEL=P1 CAPTURE_BLOCK=1 CAPTURE_ORIENTATION=sideways DATASET_NAME=datassb_side_v1 CAPTURES_PER_BLOCK=10000 matlab -batch "run('src/matlab/capture_datassb_dataset_block_timestamps.m')"
%
% Main output:
%   dataSSB: 360 x 6 x N complex single
%
% Useful validated SSB region:
%   full 30-RB grid: dataSSB(:, :, i)
%   pure SSB:        dataSSB(61:300, 2:5, i)
%
% Timestamp outputs:
%   captureStartUnixTime
%   captureEndUnixTime
%   captureMidUnixTime
%   ssbTimingReferenceUnixTime
%   ssbStartUnixTimeApprox
%   ssbRelativeTimeFromBlockStartSeconds
%   ssbInterArrivalSeconds
%
% Notes:
%   - captureStartUnixTime is taken immediately before capture().
%   - ssbTimingReferenceUnixTime = captureStartUnixTime + timingOffset / SampleRate.
%   - This is not a hardware timestamp. It is a host-clock timestamp corrected
%     by the sample-level timing estimate inside the captured buffer.
%   - Dynamic timing is still estimated for every capture.
%   - Fixed frequency offset is estimated during warmup and optionally refreshed.

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
% Configuration from environment variables
% -------------------------------------------------------------------------

cfg = struct();

cfg.DatasetName = readEnvString("DATASET_NAME", "datassb_side_v1");
cfg.Label = readEnvString("CAPTURE_LABEL", "");
cfg.BlockIndex = readEnvDouble("CAPTURE_BLOCK", NaN);
cfg.CapturesPerBlock = readEnvDouble("CAPTURES_PER_BLOCK", 10000);

if strlength(cfg.Label) == 0
    error("CAPTURE_LABEL is required. Valid labels: empty, P1, P2, P3, P4, P5.");
end

validLabels = ["empty", "P1", "P2", "P3", "P4", "P5"];

if ~any(cfg.Label == validLabels)
    error("Invalid CAPTURE_LABEL=%s. Valid labels: empty, P1, P2, P3, P4, P5.", cfg.Label);
end

if isnan(cfg.BlockIndex) || cfg.BlockIndex < 1
    error("CAPTURE_BLOCK is required and must be >= 1.");
end

if cfg.Label == "empty"
    cfg.Orientation = readEnvString("CAPTURE_ORIENTATION", "none");
else
    cfg.Orientation = readEnvString("CAPTURE_ORIENTATION", "sideways");
end

cfg.OperatorNote = readEnvString("CAPTURE_NOTE", "");

% Dataset design
cfg.TotalCaptures = cfg.CapturesPerBlock;

% Warmup calibration
cfg.NumWarmupCaptures = readEnvDouble("WARMUP_CAPTURES", 30);
cfg.MinValidWarmupCaptures = 10;

% Frequency correction
cfg.FrequencyCorrectionSign = readEnvDouble("FREQ_CORRECTION_SIGN", -1);

% Periodic frequency resync.
% This does not replace dynamic timing. Timing is still estimated every capture.
% 0 disables periodic resync.
cfg.ResyncEveryNCaptures = readEnvDouble("RESYNC_EVERY_N", 2000);
cfg.ResyncCaptures = readEnvDouble("RESYNC_CAPTURES", 8);

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
cfg.NumSymbolsToSave = 6;
cfg.NSlot = 0;

% Capture duration: 20 ms
cfg.FramesPerCapture = 1;
cfg.CaptureDurationSeconds = (cfg.FramesPerCapture + 1) * 10e-3;

% SSB placement inside saved dataSSB.
% Previous validation showed that SSB starts at column 2:
%   dataSSB(61:300, 2:5, i)
cfg.ExpectedSSBColumnStart = readEnvDouble("EXPECTED_SSB_COLUMN_START", 2);

% Approximate OFDM symbol duration including CP.
% For SCS 30 kHz, slot duration is 0.5 ms and there are 14 OFDM symbols.
cfg.AvgOFDMSymbolDurationWithCPSeconds = (1e-3 / (2^1)) / 14;

% Progress and validation
cfg.ProgressEvery = 500;
cfg.IgnoreFirstNForSteadyRate = 5;

cfg.EnablePostValidation = true;
cfg.NumPSSSSSValidationCaptures = 200;
cfg.EnablePBCHValidation = true;
cfg.NumPBCHValidationCaptures = 50;

% Output paths
timestamp = datestr(now, "yyyymmdd_HHMMSS");

cfg.OutputRoot = fullfile(projectRoot, "data", "dataset_datassb", char(cfg.DatasetName));
cfg.LabelDir = fullfile(cfg.OutputRoot, char(cfg.Label));
cfg.BlockDir = fullfile(cfg.LabelDir, sprintf("block_%02d", cfg.BlockIndex));

if ~exist(cfg.BlockDir, "dir")
    mkdir(cfg.BlockDir);
end

cfg.SessionId = string(sprintf( ...
    "%s_%s_block%02d_%s", ...
    char(cfg.DatasetName), ...
    char(cfg.Label), ...
    cfg.BlockIndex, ...
    timestamp));

cfg.OutputFile = fullfile(cfg.BlockDir, char(cfg.SessionId + ".mat"));
cfg.MetadataFile = fullfile(cfg.BlockDir, char(cfg.SessionId + "_metadata.json"));

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

fprintf("\n=== Dataset block capture setup with timestamps ===\n");
fprintf("Dataset name:           %s\n", cfg.DatasetName);
fprintf("Label:                  %s\n", cfg.Label);
fprintf("Orientation:            %s\n", cfg.Orientation);
fprintf("Block index:            %d\n", cfg.BlockIndex);
fprintf("Captures per block:     %d\n", cfg.CapturesPerBlock);
fprintf("Center frequency:       %.3f MHz\n", cfg.CenterFrequency / 1e6);
fprintf("Sample rate:            %.3f Msps\n", cfg.SampleRate / 1e6);
fprintf("SCS:                    %.0f kHz\n", cfg.SCSNumeric);
fprintf("Capture duration:       %.3f ms\n", cfg.CaptureDurationSeconds * 1000);
fprintf("Expected SSB columns:   %d:%d\n", cfg.ExpectedSSBColumnStart, cfg.ExpectedSSBColumnStart + 3);
fprintf("Resync every N:         %d\n", cfg.ResyncEveryNCaptures);
fprintf("Output MAT file:        %s\n", cfg.OutputFile);
fprintf("Output metadata file:   %s\n", cfg.MetadataFile);

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
% Preallocate output arrays
% -------------------------------------------------------------------------

numSC = cfg.DemodRB * 12;
N = cfg.TotalCaptures;

dataSSB = complex(zeros(numSC, cfg.NumSymbolsToSave, N, "single"));

validMask = false(N, 1);
errorMessages = strings(N, 1);

freqOffsets = nan(N, 1);
NID2Log = nan(N, 1);
timingOffsets = nan(N, 1);
nSymbolsGridSave = nan(N, 1);

tWallIter = nan(N, 1);
tCapture = nan(N, 1);
tProcessTotal = nan(N, 1);
tFreqApply = nan(N, 1);
tTimingEstimate = nan(N, 1);
tAlign = nan(N, 1);
tDemodSave = nan(N, 1);
tResync = nan(N, 1);

% Timestamp arrays
captureStartUnixTime = nan(N, 1);
captureEndUnixTime = nan(N, 1);
captureMidUnixTime = nan(N, 1);

ssbTimingReferenceUnixTime = nan(N, 1);
ssbStartUnixTimeApprox = nan(N, 1);
ssbTimingReferenceRelativeSeconds = nan(N, 1);
ssbStartRelativeSecondsApprox = nan(N, 1);
ssbTimeOffsetFromCaptureStartSeconds = nan(N, 1);
ssbFirstSymbolOffsetSecondsApprox = nan(N, 1);
ssbInterArrivalSeconds = nan(N, 1);

blockStartDateTimeUTC = datetime("now", "TimeZone", "UTC");
blockStartUnixTime = posixtime(blockStartDateTimeUTC);
blockStartIsoUTC = string(blockStartDateTimeUTC, "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'");

%% ------------------------------------------------------------------------
% Main capture loop
% -------------------------------------------------------------------------

fprintf("\n=== Starting dataset block capture ===\n");
fprintf("No countdown. Capturing starts now.\n\n");

tExperiment = tic;

for captureIdx = 1:N

    tLoop = tic;

    % Capture 20 ms IQ.
    % This timestamp is taken immediately before the blocking capture() call.
    captureStartUnixTime(captureIdx) = posixtime(datetime("now", "TimeZone", "UTC"));

    t0 = tic;
    waveform = capture(rx, captureDuration);
    tCapture(captureIdx) = toc(t0);

    captureEndUnixTime(captureIdx) = posixtime(datetime("now", "TimeZone", "UTC"));
    captureMidUnixTime(captureIdx) = 0.5 * (captureStartUnixTime(captureIdx) + captureEndUnixTime(captureIdx));

    % Optional periodic frequency resync
    if cfg.ResyncEveryNCaptures > 0 && captureIdx > 1 && mod(captureIdx - 1, cfg.ResyncEveryNCaptures) == 0
        t0 = tic;

        fprintf("\nResync at capture %d...\n", captureIdx);

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

        tResync(captureIdx) = toc(t0);
    end

    % Process one waveform
    result = processOneWaveformFixedFreqDynamicTiming(waveform, cfg, syncState);

    if result.Success
        dataSSB(:, :, captureIdx) = result.DataSSB;
        validMask(captureIdx) = true;

        freqOffsets(captureIdx) = syncState.FrequencyOffsetHz;
        NID2Log(captureIdx) = syncState.NID2;
        timingOffsets(captureIdx) = result.TimingOffsetUsed;
        nSymbolsGridSave(captureIdx) = result.NSymbolsGridSave;

        % Timestamp of the timing reference found by nrTimingEstimate.
        % This is the most sample-accurate timestamp we can infer inside the
        % captured buffer using the host capture-start time.
        ssbTimeOffsetFromCaptureStartSeconds(captureIdx) = result.TimingOffsetUsed / cfg.SampleRate;
        ssbTimingReferenceUnixTime(captureIdx) = captureStartUnixTime(captureIdx) + ssbTimeOffsetFromCaptureStartSeconds(captureIdx);

        % Approximate timestamp of the first SSB OFDM symbol inside dataSSB.
        % Validated layout normally gives SSB columns 2:5, so this adds one
        % average OFDM-symbol duration when ExpectedSSBColumnStart = 2.
        ssbFirstSymbolOffsetSecondsApprox(captureIdx) = ...
            (cfg.ExpectedSSBColumnStart - 1) * cfg.AvgOFDMSymbolDurationWithCPSeconds;

        ssbStartUnixTimeApprox(captureIdx) = ...
            ssbTimingReferenceUnixTime(captureIdx) + ssbFirstSymbolOffsetSecondsApprox(captureIdx);

        ssbTimingReferenceRelativeSeconds(captureIdx) = ...
            ssbTimingReferenceUnixTime(captureIdx) - blockStartUnixTime;

        ssbStartRelativeSecondsApprox(captureIdx) = ...
            ssbStartUnixTimeApprox(captureIdx) - blockStartUnixTime;

    else
        validMask(captureIdx) = false;
        errorMessages(captureIdx) = result.ErrorMessage;
    end

    tProcessTotal(captureIdx) = result.TProcessTotal;
    tFreqApply(captureIdx) = result.TFreqApply;
    tTimingEstimate(captureIdx) = result.TTimingEstimate;
    tAlign(captureIdx) = result.TAlign;
    tDemodSave(captureIdx) = result.TDemodSave;

    tWallIter(captureIdx) = toc(tLoop);

    if mod(captureIdx, cfg.ProgressEvery) == 0
        elapsedNow = toc(tExperiment);
        validNow = sum(validMask);

        fprintf("Completed %5d/%5d | valid=%5d | elapsed=%.1f s | valid rate=%.2f dataSSB/s\n", ...
            captureIdx, N, validNow, elapsedNow, validNow / elapsedNow);
    end
end

elapsedExperiment = toc(tExperiment);

blockEndDateTimeUTC = datetime("now", "TimeZone", "UTC");
blockEndUnixTime = posixtime(blockEndDateTimeUTC);
blockEndIsoUTC = string(blockEndDateTimeUTC, "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'");

% Inter-arrival time between valid captured SSB timing references.
validIdx = find(validMask);

if numel(validIdx) >= 2
    ssbInterArrivalSeconds(validIdx(2:end)) = diff(ssbTimingReferenceUnixTime(validIdx));
end

%% ------------------------------------------------------------------------
% Summary
% -------------------------------------------------------------------------

validCount = sum(validMask);
failedCount = N - validCount;

steadyIdx = validMask;
steadyIdx(1:min(cfg.IgnoreFirstNForSteadyRate, N)) = false;

steadyTotalTime = sum(tWallIter(steadyIdx), "omitnan");
steadyValidCount = sum(steadyIdx);

summary = struct();
summary.DatasetName = cfg.DatasetName;
summary.Label = cfg.Label;
summary.Orientation = cfg.Orientation;
summary.BlockIndex = cfg.BlockIndex;
summary.TotalCaptures = N;
summary.ValidDataSSB = validCount;
summary.FailedCaptures = failedCount;
summary.ElapsedSeconds = elapsedExperiment;
summary.ValidDataSSBPerSecondTotal = validCount / elapsedExperiment;
summary.SuccessRatePercent = 100 * validCount / N;
summary.SteadyValidDataSSB = steadyValidCount;
summary.SteadySecondsFromWallIter = steadyTotalTime;
summary.SteadyValidDataSSBPerSecond = steadyValidCount / steadyTotalTime;

summary.MeanWallIterSeconds = mean(tWallIter(validMask), "omitnan");
summary.MedianWallIterSeconds = median(tWallIter(validMask), "omitnan");
summary.MeanCaptureTimeSeconds = mean(tCapture(validMask), "omitnan");
summary.MedianCaptureTimeSeconds = median(tCapture(validMask), "omitnan");
summary.MeanProcessTimeSeconds = mean(tProcessTotal(validMask), "omitnan");
summary.MedianProcessTimeSeconds = median(tProcessTotal(validMask), "omitnan");

summary.BlockStartUnixTime = blockStartUnixTime;
summary.BlockEndUnixTime = blockEndUnixTime;
summary.BlockStartIsoUTC = blockStartIsoUTC;
summary.BlockEndIsoUTC = blockEndIsoUTC;

summary.MedianSSBInterArrivalSeconds = median(ssbInterArrivalSeconds(validMask), "omitnan");
summary.MeanSSBInterArrivalSeconds = mean(ssbInterArrivalSeconds(validMask), "omitnan");
summary.P05SSBInterArrivalSeconds = prctile(ssbInterArrivalSeconds(validMask), 5);
summary.P95SSBInterArrivalSeconds = prctile(ssbInterArrivalSeconds(validMask), 95);

fprintf("\n=== Capture summary ===\n");
fprintf("Dataset name:                  %s\n", summary.DatasetName);
fprintf("Label:                         %s\n", summary.Label);
fprintf("Orientation:                   %s\n", summary.Orientation);
fprintf("Block index:                   %d\n", summary.BlockIndex);
fprintf("Total captures:                %d\n", summary.TotalCaptures);
fprintf("Valid dataSSB:                 %d\n", summary.ValidDataSSB);
fprintf("Failed captures:               %d\n", summary.FailedCaptures);
fprintf("Elapsed wall time:             %.3f s\n", summary.ElapsedSeconds);
fprintf("Valid dataSSB/s total:         %.3f\n", summary.ValidDataSSBPerSecondTotal);
fprintf("Steady valid dataSSB/s:        %.3f\n", summary.SteadyValidDataSSBPerSecond);
fprintf("Success rate:                  %.2f %%\n", summary.SuccessRatePercent);
fprintf("Median wall iter time:         %.5f s\n", summary.MedianWallIterSeconds);
fprintf("Median capture time:           %.5f s\n", summary.MedianCaptureTimeSeconds);
fprintf("Median process time:           %.5f s\n", summary.MedianProcessTimeSeconds);
fprintf("Block start UTC:               %s\n", summary.BlockStartIsoUTC);
fprintf("Block end UTC:                 %s\n", summary.BlockEndIsoUTC);
fprintf("Median SSB inter-arrival:      %.5f s\n", summary.MedianSSBInterArrivalSeconds);
fprintf("Mean SSB inter-arrival:        %.5f s\n", summary.MeanSSBInterArrivalSeconds);

fprintf("\n=== Timing summary, valid captures only ===\n");
printMetric("tWallIter", tWallIter(validMask));
printMetric("tCapture", tCapture(validMask));
printMetric("tProcessTotal", tProcessTotal(validMask));
printMetric("tFreqApply", tFreqApply(validMask));
printMetric("tTimingEstimate", tTimingEstimate(validMask));
printMetric("tAlign", tAlign(validMask));
printMetric("tDemodSave", tDemodSave(validMask));
printMetric("tResync", tResync(~isnan(tResync)));
printMetric("ssbInterArrival", ssbInterArrivalSeconds(validMask));

%% ------------------------------------------------------------------------
% Post-capture validation
% -------------------------------------------------------------------------

validation = struct();

if cfg.EnablePostValidation
    fprintf("\n=== Post-capture SSB validation ===\n");

    validation = validateCapturedDataSSB(dataSSB, validMask, cfg, syncState);

    fprintf("Checked captures:              %d\n", validation.NumChecked);
    fprintf("Best SSB column start mode:    %d\n", validation.BestColumnStartMode);
    fprintf("Column start counts [1 2 3]:   [%d %d %d]\n", ...
        validation.ColumnStartCounts(1), ...
        validation.ColumnStartCounts(2), ...
        validation.ColumnStartCounts(3));
    fprintf("PSS corr median:               %.3f\n", validation.PSSCorrMedian);
    fprintf("PSS corr min:                  %.3f\n", validation.PSSCorrMin);
    fprintf("SSS corr median:               %.3f\n", validation.SSSCorrMedian);
    fprintf("SSS corr min:                  %.3f\n", validation.SSSCorrMin);
    fprintf("Detected NCellID mode:         %d\n", validation.NCellIDMode);

    if cfg.EnablePBCHValidation
        fprintf("PBCH/BCH CRC OK:               %d/%d = %.2f %%\n", ...
            validation.PBCHCrcOK, ...
            validation.NumPBCHChecked, ...
            validation.PBCHCrcOKPercent);
        fprintf("Detected ibar_SSB mode:        %d\n", validation.IbarSSBMode);
    end
end

%% ------------------------------------------------------------------------
% Save results
% -------------------------------------------------------------------------

labelName = cfg.Label;
orientationName = cfg.Orientation;
datasetName = cfg.DatasetName;
blockIndex = cfg.BlockIndex;
sessionId = cfg.SessionId;

save( ...
    cfg.OutputFile, ...
    "dataSSB", ...
    "validMask", ...
    "errorMessages", ...
    "freqOffsets", ...
    "NID2Log", ...
    "timingOffsets", ...
    "nSymbolsGridSave", ...
    "captureStartUnixTime", ...
    "captureEndUnixTime", ...
    "captureMidUnixTime", ...
    "ssbTimingReferenceUnixTime", ...
    "ssbStartUnixTimeApprox", ...
    "ssbTimingReferenceRelativeSeconds", ...
    "ssbStartRelativeSecondsApprox", ...
    "ssbTimeOffsetFromCaptureStartSeconds", ...
    "ssbFirstSymbolOffsetSecondsApprox", ...
    "ssbInterArrivalSeconds", ...
    "blockStartUnixTime", ...
    "blockEndUnixTime", ...
    "blockStartIsoUTC", ...
    "blockEndIsoUTC", ...
    "tWallIter", ...
    "tCapture", ...
    "tProcessTotal", ...
    "tFreqApply", ...
    "tTimingEstimate", ...
    "tAlign", ...
    "tDemodSave", ...
    "tResync", ...
    "cfg", ...
    "summary", ...
    "syncState", ...
    "validation", ...
    "labelName", ...
    "orientationName", ...
    "datasetName", ...
    "blockIndex", ...
    "sessionId", ...
    "-v7.3");

writeMetadataJson(cfg.MetadataFile, cfg, summary, syncState, validation);

fprintf("\nSaved MAT file:\n%s\n", cfg.OutputFile);
fprintf("\nSaved metadata file:\n%s\n", cfg.MetadataFile);

release(rx);

%% ------------------------------------------------------------------------
% Local functions
% -------------------------------------------------------------------------

function value = readEnvString(name, defaultValue)
    raw = getenv(char(name));

    if isempty(raw)
        value = string(defaultValue);
    else
        value = string(raw);
    end
end


function value = readEnvDouble(name, defaultValue)
    raw = getenv(char(name));

    if isempty(raw)
        value = defaultValue;
    else
        value = str2double(raw);

        if isnan(value)
            error("Environment variable %s must be numeric. Got: %s", name, raw);
        end
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


function result = processOneWaveformFixedFreqDynamicTiming(waveform, cfg, syncState)
    result = struct();

    result.Success = false;
    result.ErrorMessage = "";
    result.DataSSB = [];
    result.NSymbolsGridSave = NaN;

    result.TProcessTotal = NaN;
    result.TFreqApply = NaN;
    result.TTimingEstimate = NaN;
    result.TAlign = NaN;
    result.TDemodSave = NaN;
    result.TimingOffsetUsed = NaN;

    tAll = tic;

    try
        if isnan(syncState.FrequencyOffsetHz) || isnan(syncState.NID2)
            error("Invalid sync state.");
        end

        % Fixed frequency correction
        t0 = tic;
        correctedWaveform = applyFrequencyCorrection( ...
            waveform, ...
            syncState.FrequencyOffsetHz, ...
            cfg.SampleRate, ...
            cfg.FrequencyCorrectionSign);
        result.TFreqApply = toc(t0);

        % Dynamic timing estimate per capture
        t0 = tic;
        timingOffset = estimateTimingOffset(correctedWaveform, syncState.NID2, cfg);
        result.TTimingEstimate = toc(t0);
        result.TimingOffsetUsed = timingOffset;

        % Timing alignment
        t0 = tic;
        correctedWaveformAligned = correctedWaveform(1 + timingOffset:end, :);
        result.TAlign = toc(t0);

        % OFDM demodulate 30 RB and save first 6 symbols
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


function validation = validateCapturedDataSSB(dataSSB, validMask, cfg, syncState)
    validation = struct();

    validIdx = find(validMask);

    if isempty(validIdx)
        validation.NumChecked = 0;
        validation.ColumnStartCounts = [0 0 0];
        validation.BestColumnStartMode = NaN;
        validation.PSSCorrMedian = NaN;
        validation.PSSCorrMin = NaN;
        validation.SSSCorrMedian = NaN;
        validation.SSSCorrMin = NaN;
        validation.NCellIDMode = NaN;
        validation.PBCHCrcOK = NaN;
        validation.NumPBCHChecked = 0;
        validation.PBCHCrcOKPercent = NaN;
        validation.IbarSSBMode = NaN;
        return;
    end

    nCheck = min(cfg.NumPSSSSSValidationCaptures, numel(validIdx));
    checkIdx = validIdx(round(linspace(1, numel(validIdx), nCheck)));
    checkIdx = unique(checkIdx, "stable");
    nCheck = numel(checkIdx);

    ssbFreqOrigin = 12 * (cfg.DemodRB - cfg.NRBSSB) / 2 + 1;
    ssbRows = ssbFreqOrigin:(ssbFreqOrigin + cfg.NRBSSB * 12 - 1);

    NID2 = syncState.NID2;

    pssRef = nrPSS(NID2);
    pssIndices = nrPSSIndices;
    sssIndices = nrSSSIndices;

    sssRefMat = zeros(127, 336);

    for nid1 = 0:335
        ncellidCandidate = 3 * nid1 + NID2;
        sssRefMat(:, nid1 + 1) = nrSSS(ncellidCandidate);
    end

    bestColStart = nan(nCheck, 1);
    bestPSSCorr = nan(nCheck, 1);
    bestSSSCorr = nan(nCheck, 1);
    NID1Log = nan(nCheck, 1);
    NCellIDLog = nan(nCheck, 1);

    for m = 1:nCheck
        idx = checkIdx(m);

        localBestPSS = -inf;
        localBestSSS = -inf;
        localBestStart = NaN;
        localBestNID1 = NaN;

        for colStart = 1:3
            rxGridSSB = double(dataSSB(ssbRows, colStart:(colStart + 3), idx));

            pssRx = nrExtractResources(pssIndices, rxGridSSB);
            pssCorr = abs(pssRef' * pssRx) / (norm(pssRef) * norm(pssRx) + eps);

            sssRx = nrExtractResources(sssIndices, rxGridSSB);
            sssCorrs = abs(sssRefMat' * sssRx) ./ ((vecnorm(sssRefMat).') * norm(sssRx) + eps);

            [sssCorr, nid1Idx] = max(sssCorrs);
            nid1 = nid1Idx - 1;

            score = pssCorr + sssCorr;

            if score > (localBestPSS + localBestSSS)
                localBestPSS = pssCorr;
                localBestSSS = sssCorr;
                localBestStart = colStart;
                localBestNID1 = nid1;
            end
        end

        bestColStart(m) = localBestStart;
        bestPSSCorr(m) = localBestPSS;
        bestSSSCorr(m) = localBestSSS;
        NID1Log(m) = localBestNID1;
        NCellIDLog(m) = 3 * localBestNID1 + NID2;
    end

    validation.NumChecked = nCheck;
    validation.CheckedIndices = checkIdx;
    validation.SSBRows = [ssbRows(1), ssbRows(end)];
    validation.BestColumnStart = bestColStart;
    validation.BestColumnStartMode = mode(bestColStart);
    validation.ColumnStartCounts = [ ...
        sum(bestColStart == 1), ...
        sum(bestColStart == 2), ...
        sum(bestColStart == 3)];

    validation.PSSCorrMedian = median(bestPSSCorr, "omitnan");
    validation.PSSCorrMin = min(bestPSSCorr);
    validation.PSSCorrP05 = prctile(bestPSSCorr, 5);
    validation.PSSCorrP95 = prctile(bestPSSCorr, 95);

    validation.SSSCorrMedian = median(bestSSSCorr, "omitnan");
    validation.SSSCorrMin = min(bestSSSCorr);
    validation.SSSCorrP05 = prctile(bestSSSCorr, 5);
    validation.SSSCorrP95 = prctile(bestSSSCorr, 95);

    validation.NID1Mode = mode(NID1Log);
    validation.NCellIDMode = mode(NCellIDLog);

    validation.PBCHCrcOK = NaN;
    validation.NumPBCHChecked = 0;
    validation.PBCHCrcOKPercent = NaN;
    validation.IbarSSBMode = NaN;

    if cfg.EnablePBCHValidation
        nPBCHCheck = min(cfg.NumPBCHValidationCaptures, nCheck);
        crcOK = false(nPBCHCheck, 1);
        ibarLog = nan(nPBCHCheck, 1);

        for m = 1:nPBCHCheck
            idx = checkIdx(m);
            colStart = bestColStart(m);

            rxGridSSB = double(dataSSB(ssbRows, colStart:(colStart + 3), idx));
            ncellid = NCellIDLog(m);

            try
                dmrsIndices = nrPBCHDMRSIndices(ncellid);

                dmrsEst = zeros(1, 8);

                for ibar_SSB = 0:7
                    refGrid2 = zeros([cfg.NRBSSB * 12, 4]);
                    refGrid2(dmrsIndices) = nrPBCHDMRS(ncellid, ibar_SSB);

                    [hTmp, nTmp] = nrChannelEstimate(rxGridSSB, refGrid2, "AveragingWindow", [0 1]);

                    dmrsEst(ibar_SSB + 1) = 10 * log10(mean(abs(hTmp(:)).^2) / (nTmp + eps));
                end

                ibar_SSB = find(dmrsEst == max(dmrsEst), 1, "first") - 1;
                ibarLog(m) = ibar_SSB;

                if cfg.CenterFrequency <= 3e9
                    L_max = 4;
                    v = mod(ibar_SSB, L_max);
                else
                    L_max = 8;
                    v = ibar_SSB;
                end

                refGrid3 = zeros([cfg.NRBSSB * 12, 4]);
                refGrid3(dmrsIndices) = nrPBCHDMRS(ncellid, ibar_SSB);
                refGrid3(sssIndices) = nrSSS(ncellid);

                [hest, nest] = nrChannelEstimate(rxGridSSB, refGrid3, "AveragingWindow", [0 1]);

                [pbchIndices, pbchIndicesInfo] = nrPBCHIndices(ncellid);

                pbchRx = nrExtractResources(pbchIndices, rxGridSSB);
                pbchHest = nrExtractResources(pbchIndices, hest);

                [pbchEq, csi] = nrEqualizeMMSE(pbchRx, pbchHest, nest);

                Qm = pbchIndicesInfo.G / pbchIndicesInfo.Gd;
                csi = repmat(csi.', Qm, 1);
                csi = reshape(csi, [], 1);

                pbchBits = nrPBCHDecode(pbchEq, ncellid, v, nest);
                pbchBits = pbchBits .* csi;

                polarListLength = 8;
                [~, crcBCH] = nrBCHDecode(pbchBits, polarListLength, L_max, ncellid);

                crcOK(m) = (crcBCH == 0);

            catch
                crcOK(m) = false;
            end
        end

        validation.PBCHCrcOK = sum(crcOK);
        validation.NumPBCHChecked = nPBCHCheck;
        validation.PBCHCrcOKPercent = 100 * sum(crcOK) / nPBCHCheck;

        validIbar = ibarLog(~isnan(ibarLog));

        if ~isempty(validIbar)
            validation.IbarSSBMode = mode(validIbar);
        end
    end
end


function writeMetadataJson(metadataFile, cfg, summary, syncState, validation)
    metadata = struct();
    metadata.cfg = cfg;
    metadata.summary = summary;
    metadata.syncState = syncState;
    metadata.validation = validation;

    jsonText = jsonencode(metadata, PrettyPrint = true);

    fid = fopen(metadataFile, "w");

    if fid < 0
        warning("Could not write metadata JSON: %s", metadataFile);
        return;
    end

    fprintf(fid, "%s", jsonText);
    fclose(fid);
end


function printMetric(name, values)
    values = values(~isnan(values));

    if isempty(values)
        fprintf("%-18s | no valid values\n", name);
        return;
    end

    fprintf("%-18s | N=%5d | mean=%.5f | median=%.5f | p95=%.5f | min=%.5f | max=%.5f\n", ...
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
