%% Fast asynchronous dataSSB capture
% Goal:
%   Capture as many aligned SSB resource-grid snapshots as possible.
%
% Output:
%   dataSSB: 360 x 6 x N complex single
%
% Important:
%   This script does NOT decode PBCH.
%   This script does NOT estimate hSSB.
%   This script does NOT scan DMRS / ibar_SSB.
%   This script does NOT plot.
%
% It only:
%   1) captures 20 ms IQ waveform,
%   2) corrects frequency using SSB/PSS,
%   3) estimates timing using PSS,
%   4) OFDM-demodulates 30 RB,
%   5) saves the first 6 aligned symbols as dataSSB.

clear;
clc;

%% Add local helper paths
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

cfg.TotalCaptures = 50;

% If true, capture runs in the main MATLAB thread and processing runs in
% backgroundPool workers. This overlaps capture and processing.
cfg.UseAsyncProcessing = false;

% Number of processing jobs allowed to wait/run in background.
% If this is too high, memory grows. 4 to 8 is reasonable.
cfg.MaxInFlight = 6;

% Frequency correction mode:
%
% "perCapture":
%   Safest mode. Runs mySSBurstFrequencyCorrectFast on every capture.
%
% "fixedAfterWarmup":
%   Faster experimental mode. First estimates median frequency offset and
%   NID2 from warmup captures, then applies fixed correction.
%
% Recommendation:
%   First run with "perCapture".
%   If stable, try "fixedAfterWarmup".
cfg.FrequencyMode = "perCapture";
cfg.NumWarmupCaptures = 20;

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
cfg.DemodRB = 30;            % 30 RB = 360 subcarriers for dataSSB
cfg.NumSymbolsToSave = 6;    % dataSSB = 360 x 6 x N
cfg.NSlot = 0;

% Capture duration.
% With framesPerCapture = 1, this captures 20 ms.
cfg.FramesPerCapture = 1;
cfg.CaptureDurationSeconds = (cfg.FramesPerCapture + 1) * 10e-3;

% Output
timestamp = datestr(now, "yyyymmdd_HHMMSS");
cfg.OutputDir = fullfile(projectRoot, "data", "fast_datassb");
cfg.OutputFile = fullfile(cfg.OutputDir, "datassb_fast_async_" + string(timestamp) + ".mat");

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
scs = scsOptions(1);
cfg.SCS = scs;
cfg.SCSNumeric = double(extract(scs, digitsPattern));

ofdmInfo = nrOFDMInfo(cfg.NRBSSB, cfg.SCSNumeric);
rx.SampleRate = ofdmInfo.SampleRate;

cfg.SampleRate = rx.SampleRate;
cfg.CenterFrequency = rx.CenterFrequency;
cfg.SSBBlockPattern = hSynchronizationRasterInfo.getBlockPattern(cfg.SCS, rx.CenterFrequency);
cfg.SearchBW = 0.75 * cfg.SCSNumeric;
cfg.DisplayFigure = false;

captureDuration = seconds(cfg.CaptureDurationSeconds);

fprintf("\n=== Fast dataSSB capture setup ===\n");
fprintf("Center frequency: %.3f MHz\n", cfg.CenterFrequency / 1e6);
fprintf("Sample rate: %.3f Msps\n", cfg.SampleRate / 1e6);
fprintf("SCS: %.0f kHz\n", cfg.SCSNumeric);
fprintf("Capture duration: %.3f ms\n", cfg.CaptureDurationSeconds * 1000);
fprintf("Total capture calls: %d\n", cfg.TotalCaptures);
fprintf("Async processing: %d\n", cfg.UseAsyncProcessing);
fprintf("Frequency mode: %s\n", cfg.FrequencyMode);
fprintf("Output file: %s\n", cfg.OutputFile);

%% ------------------------------------------------------------------------
% Optional warmup for fixed frequency mode
% -------------------------------------------------------------------------

cfg.FixedFrequencyOffsetHz = NaN;
cfg.FixedNID2 = NaN;

if cfg.FrequencyMode == "fixedAfterWarmup"
    fprintf("\n=== Warmup acquisition ===\n");

    warmupOffsets = nan(cfg.NumWarmupCaptures, 1);
    warmupNID2 = nan(cfg.NumWarmupCaptures, 1);

    for k = 1:cfg.NumWarmupCaptures
        waveform = capture(rx, captureDuration);

        try
            [~, freqOffset, NID2] = mySSBurstFrequencyCorrectFast( ...
                waveform, ...
                cfg.SSBBlockPattern, ...
                cfg.SampleRate, ...
                cfg.SearchBW, ...
                cfg.DisplayFigure);

            warmupOffsets(k) = freqOffset;
            warmupNID2(k) = NID2;

            fprintf("Warmup %d/%d | freqOffset=%.2f Hz | NID2=%d\n", ...
                k, cfg.NumWarmupCaptures, freqOffset, NID2);
        catch ME
            fprintf("Warmup %d/%d failed: %s\n", ...
                k, cfg.NumWarmupCaptures, ME.message);
        end
    end

    validWarmup = ~isnan(warmupOffsets) & ~isnan(warmupNID2);

    if sum(validWarmup) < 3
        error("Not enough valid warmup captures. Use cfg.FrequencyMode = ""perCapture"".");
    end

    cfg.FixedFrequencyOffsetHz = median(warmupOffsets(validWarmup), "omitnan");
    cfg.FixedNID2 = mode(warmupNID2(validWarmup));

    fprintf("\nFixed frequency offset: %.2f Hz\n", cfg.FixedFrequencyOffsetHz);
    fprintf("Fixed NID2: %d\n", cfg.FixedNID2);
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

tCapture = nan(cfg.TotalCaptures, 1);
tProcessTotal = nan(cfg.TotalCaptures, 1);
tFreqCorrection = nan(cfg.TotalCaptures, 1);
tTimingEstimate = nan(cfg.TotalCaptures, 1);
tDemodSave = nan(cfg.TotalCaptures, 1);

%% ------------------------------------------------------------------------
% Start asynchronous pool if requested
% -------------------------------------------------------------------------

if cfg.UseAsyncProcessing
    try
        pool = backgroundPool;
        fprintf("\nUsing backgroundPool with %d workers.\n", pool.NumWorkers);
    catch ME
        warning("backgroundPool unavailable: %s", ME.message);
        warning("Falling back to sequential mode.");
        cfg.UseAsyncProcessing = false;
    end
end

%% ------------------------------------------------------------------------
% Main capture loop
% -------------------------------------------------------------------------

fprintf("\n=== Starting capture ===\n");

tExperiment = tic;

nCapturedWaveforms = 0;
nCompletedJobs = 0;
nSubmittedJobs = 0;

if cfg.UseAsyncProcessing
    futures = parallel.FevalFuture.empty(0, 1);
    futureCaptureIdx = zeros(0, 1);

    nextCaptureIdx = 1;

    while nCompletedJobs < cfg.TotalCaptures

        % Submit new captures while there is queue capacity.
        while nextCaptureIdx <= cfg.TotalCaptures && numel(futures) < cfg.MaxInFlight

            t0 = tic;
            waveform = capture(rx, captureDuration);
            tCapture(nextCaptureIdx) = toc(t0);

            nCapturedWaveforms = nCapturedWaveforms + 1;

            f = parfeval( ...
                backgroundPool, ...
                @processOneWaveformToDataSSB, ...
                1, ...
                waveform, ...
                cfg);

            futures(end + 1, 1) = f;
            futureCaptureIdx(end + 1, 1) = nextCaptureIdx;

            nSubmittedJobs = nSubmittedJobs + 1;

            nextCaptureIdx = nextCaptureIdx + 1;
        end

        % Fetch one completed processing job.
        if ~isempty(futures)
            [completedPosition, result] = fetchNext(futures);

            captureIdx = futureCaptureIdx(completedPosition);

            [dataSSB, validMask, errorMessages, freqOffsets, NID2Log, ...
                timingOffsets, nSymbolsGridSave, tProcessTotal, ...
                tFreqCorrection, tTimingEstimate, tDemodSave] = storeResult( ...
                    dataSSB, validMask, errorMessages, freqOffsets, NID2Log, ...
                    timingOffsets, nSymbolsGridSave, tProcessTotal, ...
                    tFreqCorrection, tTimingEstimate, tDemodSave, ...
                    captureIdx, result);

            futures(completedPosition) = [];
            futureCaptureIdx(completedPosition) = [];

            nCompletedJobs = nCompletedJobs + 1;

            if mod(nCompletedJobs, 50) == 0
                elapsedNow = toc(tExperiment);
                fprintf("Completed %4d/%4d | valid=%4d | elapsed=%.2f s | valid rate=%.2f SSB/s\n", ...
                    nCompletedJobs, cfg.TotalCaptures, sum(validMask), elapsedNow, sum(validMask) / elapsedNow);
            end
        end
    end

else
    for captureIdx = 1:cfg.TotalCaptures

        t0 = tic;
        waveform = capture(rx, captureDuration);
        tCapture(captureIdx) = toc(t0);

        nCapturedWaveforms = nCapturedWaveforms + 1;

        result = processOneWaveformToDataSSB(waveform, cfg);

        [dataSSB, validMask, errorMessages, freqOffsets, NID2Log, ...
            timingOffsets, nSymbolsGridSave, tProcessTotal, ...
            tFreqCorrection, tTimingEstimate, tDemodSave] = storeResult( ...
                dataSSB, validMask, errorMessages, freqOffsets, NID2Log, ...
                timingOffsets, nSymbolsGridSave, tProcessTotal, ...
                tFreqCorrection, tTimingEstimate, tDemodSave, ...
                captureIdx, result);

        nCompletedJobs = nCompletedJobs + 1;

        if mod(nCompletedJobs, 50) == 0
            elapsedNow = toc(tExperiment);
            fprintf("Completed %4d/%4d | valid=%4d | elapsed=%.2f s | valid rate=%.2f SSB/s\n", ...
                nCompletedJobs, cfg.TotalCaptures, sum(validMask), elapsedNow, sum(validMask) / elapsedNow);
        end
    end
end

elapsedExperiment = toc(tExperiment);

%% ------------------------------------------------------------------------
% Summary
% -------------------------------------------------------------------------

validCount = sum(validMask);
failedCount = cfg.TotalCaptures - validCount;

summary = struct();
summary.TotalCaptureCalls = cfg.TotalCaptures;
summary.CapturedWaveforms = nCapturedWaveforms;
summary.CompletedJobs = nCompletedJobs;
summary.ValidDataSSB = validCount;
summary.FailedCaptures = failedCount;
summary.ElapsedSeconds = elapsedExperiment;
summary.CaptureCallsPerSecond = nCapturedWaveforms / elapsedExperiment;
summary.ValidDataSSBPerSecond = validCount / elapsedExperiment;
summary.SuccessRatePercent = 100 * validCount / cfg.TotalCaptures;
summary.MeanCaptureTimeSeconds = mean(tCapture(~isnan(tCapture)));
summary.MedianCaptureTimeSeconds = median(tCapture(~isnan(tCapture)));
summary.MeanProcessTimeSeconds = mean(tProcessTotal(validMask), "omitnan");
summary.MedianProcessTimeSeconds = median(tProcessTotal(validMask), "omitnan");

fprintf("\n=== Capture summary ===\n");
fprintf("Capture calls:              %d\n", summary.CapturedWaveforms);
fprintf("Completed jobs:             %d\n", summary.CompletedJobs);
fprintf("Valid dataSSB:              %d\n", summary.ValidDataSSB);
fprintf("Failed captures:            %d\n", summary.FailedCaptures);
fprintf("Elapsed wall time:          %.3f s\n", summary.ElapsedSeconds);
fprintf("Capture calls per second:   %.3f\n", summary.CaptureCallsPerSecond);
fprintf("Valid dataSSB per second:   %.3f\n", summary.ValidDataSSBPerSecond);
fprintf("Success rate:               %.2f %%\n", summary.SuccessRatePercent);
fprintf("Mean capture time:          %.4f s\n", summary.MeanCaptureTimeSeconds);
fprintf("Median capture time:        %.4f s\n", summary.MedianCaptureTimeSeconds);
fprintf("Mean process time:          %.4f s\n", summary.MeanProcessTimeSeconds);
fprintf("Median process time:        %.4f s\n", summary.MedianProcessTimeSeconds);

fprintf("\n=== Timing summary, valid captures only ===\n");
printMetric("tCapture", tCapture(validMask));
printMetric("tProcessTotal", tProcessTotal(validMask));
printMetric("tFreqCorrection", tFreqCorrection(validMask));
printMetric("tTimingEstimate", tTimingEstimate(validMask));
printMetric("tDemodSave", tDemodSave(validMask));

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
    "tCapture", ...
    "tProcessTotal", ...
    "tFreqCorrection", ...
    "tTimingEstimate", ...
    "tDemodSave", ...
    "cfg", ...
    "summary", ...
    "-v7.3");

fprintf("\nSaved output file:\n%s\n", cfg.OutputFile);

release(rx);

%% ------------------------------------------------------------------------
% Local functions
% -------------------------------------------------------------------------

function result = processOneWaveformToDataSSB(waveform, cfg)
    result = struct();

    result.Success = false;
    result.ErrorMessage = "";
    result.DataSSB = [];
    result.FreqOffset = NaN;
    result.NID2 = NaN;
    result.TimingOffset = NaN;
    result.NSymbolsGridSave = NaN;

    result.TProcessTotal = NaN;
    result.TFreqCorrection = NaN;
    result.TTimingEstimate = NaN;
    result.TDemodSave = NaN;

    tAll = tic;

    try
        %% Frequency correction
        t0 = tic;

        if cfg.FrequencyMode == "perCapture"
            [correctedWaveform, freqOffset, NID2] = mySSBurstFrequencyCorrectFast( ...
                waveform, ...
                cfg.SSBBlockPattern, ...
                cfg.SampleRate, ...
                cfg.SearchBW, ...
                cfg.DisplayFigure);

        elseif cfg.FrequencyMode == "fixedAfterWarmup"
            freqOffset = cfg.FixedFrequencyOffsetHz;
            NID2 = cfg.FixedNID2;
            correctedWaveform = applyFrequencyCorrection(waveform, freqOffset, cfg.SampleRate);

        else
            error("Unknown FrequencyMode: %s", cfg.FrequencyMode);
        end

        result.TFreqCorrection = toc(t0);

        %% Timing estimate using PSS
        t0 = tic;

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

        correctedWaveformAligned = correctedWaveform(1 + timingOffset:end, :);

        result.TTimingEstimate = toc(t0);

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

        %% Result
        result.Success = true;
        result.DataSSB = tmp;
        result.FreqOffset = freqOffset;
        result.NID2 = NID2;
        result.TimingOffset = timingOffset;
        result.NSymbolsGridSave = size(rxGridSave, 2);
        result.TProcessTotal = toc(tAll);

    catch ME
        result.Success = false;
        result.ErrorMessage = string(ME.message);
        result.TProcessTotal = toc(tAll);
    end
end


function y = applyFrequencyCorrection(x, freqOffsetHz, sampleRate)
    % Applies complex frequency correction.
    %
    % If the sign is wrong in your specific setup, change the minus sign
    % below to a plus sign. Use this mode only after validating with
    % FrequencyMode = "perCapture".

    n = (0:size(x, 1)-1).';
    rot = exp(-1j * 2 * pi * freqOffsetHz * n / sampleRate);
    y = x .* rot;
end


function [dataSSB, validMask, errorMessages, freqOffsets, NID2Log, ...
          timingOffsets, nSymbolsGridSave, tProcessTotal, ...
          tFreqCorrection, tTimingEstimate, tDemodSave] = storeResult( ...
          dataSSB, validMask, errorMessages, freqOffsets, NID2Log, ...
          timingOffsets, nSymbolsGridSave, tProcessTotal, ...
          tFreqCorrection, tTimingEstimate, tDemodSave, ...
          captureIdx, result)

    if result.Success
        dataSSB(:, :, captureIdx) = result.DataSSB;
        validMask(captureIdx) = true;

        freqOffsets(captureIdx) = result.FreqOffset;
        NID2Log(captureIdx) = result.NID2;
        timingOffsets(captureIdx) = result.TimingOffset;
        nSymbolsGridSave(captureIdx) = result.NSymbolsGridSave;

        tProcessTotal(captureIdx) = result.TProcessTotal;
        tFreqCorrection(captureIdx) = result.TFreqCorrection;
        tTimingEstimate(captureIdx) = result.TTimingEstimate;
        tDemodSave(captureIdx) = result.TDemodSave;
    else
        validMask(captureIdx) = false;
        errorMessages(captureIdx) = result.ErrorMessage;

        tProcessTotal(captureIdx) = result.TProcessTotal;
        tFreqCorrection(captureIdx) = result.TFreqCorrection;
        tTimingEstimate(captureIdx) = result.TTimingEstimate;
        tDemodSave(captureIdx) = result.TDemodSave;
    end
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
