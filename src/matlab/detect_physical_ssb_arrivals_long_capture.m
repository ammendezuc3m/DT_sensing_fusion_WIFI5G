%% Detect physical SSB arrivals in one continuous long capture
% Goal:
%   Estimate when SSB/PSS transmissions physically appear inside one
%   continuous IQ capture.
%
% This is different from the dataset capture loop:
%   - Dataset loop gives one processed SSB every ~60 ms because capture +
%     processing takes that long.
%   - This script captures a continuous buffer, e.g. 1 second, and detects
%     all PSS/SSB arrivals inside that buffer.
%
% Output:
%   A CSV and MAT file with one row per detected PSS/SSB arrival:
%       pss_time_madrid
%       pss_relative_time_s
%       inter_arrival_s
%       pss_metric
%       symbol_index
%       sample_index_from_capture_start
%
% Important:
%   Absolute timestamps are host-clock based, not UHD hardware timestamps.
%   Inter-arrival times inside the same buffer are the relevant part.

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
% Configuration
% -------------------------------------------------------------------------

cfg = struct();

% Long continuous capture duration.
% Start with 1 second. If memory is fine, try 2 or 5 seconds later.
cfg.LongCaptureSeconds = readEnvDouble("CAPTURE_SECONDS", 1.0);

% Warmup for fixed frequency/NID2.
cfg.NumWarmupCaptures = readEnvDouble("WARMUP_CAPTURES", 20);
cfg.MinValidWarmupCaptures = 5;

% PSS detection threshold.
% If no peaks are found, try 0.60.
% If too many false peaks are found, try 0.80.
cfg.PSSCorrThreshold = readEnvDouble("PSS_CORR_THRESHOLD", 0.70);

% Adaptive threshold control.
cfg.AdaptiveThresholdSigma = readEnvDouble("ADAPTIVE_THRESHOLD_SIGMA", 6.0);
cfg.MaxAdaptiveThreshold = readEnvDouble("MAX_ADAPTIVE_THRESHOLD", 0.85);

% Minimum distance between detected PSS peaks in OFDM symbols.
% At SCS 30 kHz, one OFDM symbol is about 35.7 us.
% 4 symbols is about 0.143 ms. This still allows close SSB candidates.
cfg.MinPeakDistanceSymbols = readEnvDouble("MIN_PEAK_DISTANCE_SYMBOLS", 4);

% Frequency correction sign.
cfg.FrequencyCorrectionSign = readEnvDouble("FREQ_CORRECTION_SIGN", -1);

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
cfg.NSlot = 0;

% The warmup captures use 20 ms.
cfg.FramesPerCapture = 1;
cfg.WarmupCaptureSeconds = (cfg.FramesPerCapture + 1) * 10e-3;

% Output
timestamp = datestr(now, "yyyymmdd_HHMMSS");
cfg.OutputDir = fullfile(projectRoot, "data", "ssb_arrival_timing");

if ~exist(cfg.OutputDir, "dir")
    mkdir(cfg.OutputDir);
end

cfg.OutputMatFile = fullfile(cfg.OutputDir, "physical_ssb_arrivals_" + string(timestamp) + ".mat");
cfg.OutputCsvFile = fullfile(cfg.OutputDir, "physical_ssb_arrivals_" + string(timestamp) + ".csv");

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

fprintf("\n=== Physical SSB arrival detection setup ===\n");
fprintf("Center frequency:        %.3f MHz\n", cfg.CenterFrequency / 1e6);
fprintf("Sample rate:             %.3f Msps\n", cfg.SampleRate / 1e6);
fprintf("SCS:                     %.0f kHz\n", cfg.SCSNumeric);
fprintf("Long capture duration:   %.3f s\n", cfg.LongCaptureSeconds);
fprintf("PSS corr threshold:      %.3f\n", cfg.PSSCorrThreshold);
fprintf("Min peak distance:       %d OFDM symbols\n", cfg.MinPeakDistanceSymbols);
fprintf("Output MAT:              %s\n", cfg.OutputMatFile);
fprintf("Output CSV:              %s\n", cfg.OutputCsvFile);

%% ------------------------------------------------------------------------
% Warmup calibration
% -------------------------------------------------------------------------

fprintf("\n=== Warmup frequency/NID2 calibration ===\n");

warmupCaptureDuration = seconds(cfg.WarmupCaptureSeconds);
syncState = calibrateFixedFrequency(rx, warmupCaptureDuration, cfg, cfg.NumWarmupCaptures);

fprintf("\n=== Initial sync state ===\n");
fprintf("Fixed frequency offset: %.2f Hz\n", syncState.FrequencyOffsetHz);
fprintf("Fixed NID2:             %d\n", syncState.NID2);
fprintf("Valid warmup captures:  %d\n", syncState.ValidWarmupCaptures);

if syncState.ValidWarmupCaptures < cfg.MinValidWarmupCaptures
    error("Not enough valid warmup captures. Check signal or increase WARMUP_CAPTURES.");
end

%% ------------------------------------------------------------------------
% Long continuous capture
% -------------------------------------------------------------------------

fprintf("\n=== Capturing continuous IQ buffer ===\n");

longCaptureDuration = seconds(cfg.LongCaptureSeconds);

captureStartDateTimeUTC = datetime("now", "TimeZone", "UTC");
captureStartUnixTime = posixtime(captureStartDateTimeUTC);

tCapture = tic;
waveform = capture(rx, longCaptureDuration);
captureElapsedSeconds = toc(tCapture);

captureEndDateTimeUTC = datetime("now", "TimeZone", "UTC");
captureEndUnixTime = posixtime(captureEndDateTimeUTC);

fprintf("Captured %d IQ samples in %.3f s wall time.\n", size(waveform, 1), captureElapsedSeconds);

%% ------------------------------------------------------------------------
% Fixed frequency correction
% -------------------------------------------------------------------------

fprintf("\n=== Applying fixed frequency correction ===\n");

t0 = tic;
correctedWaveform = applyFrequencyCorrection( ...
    waveform, ...
    syncState.FrequencyOffsetHz, ...
    cfg.SampleRate, ...
    cfg.FrequencyCorrectionSign);
tFreqApply = toc(t0);

fprintf("Frequency correction time: %.3f s\n", tFreqApply);

clear waveform;

%% ------------------------------------------------------------------------
% Initial timing alignment
% -------------------------------------------------------------------------

fprintf("\n=== Estimating initial OFDM timing ===\n");

t0 = tic;
initialTimingOffset = estimateTimingOffset(correctedWaveform, syncState.NID2, cfg);
tInitialTiming = toc(t0);

fprintf("Initial timing offset: %d samples = %.6f s\n", ...
    initialTimingOffset, initialTimingOffset / cfg.SampleRate);
fprintf("Initial timing estimate time: %.3f s\n", tInitialTiming);

correctedWaveformAligned = correctedWaveform(1 + initialTimingOffset:end, :);

clear correctedWaveform;

%% ------------------------------------------------------------------------
% OFDM demodulate full aligned buffer
% -------------------------------------------------------------------------

fprintf("\n=== OFDM demodulating full buffer ===\n");

t0 = tic;
rxGrid20 = nrOFDMDemodulate( ...
    correctedWaveformAligned, ...
    cfg.NRBSSB, ...
    cfg.SCSNumeric, ...
    cfg.NSlot, ...
    SampleRate = cfg.SampleRate);
tDemod = toc(t0);

nSymbols = size(rxGrid20, 2);

fprintf("OFDM demodulation time: %.3f s\n", tDemod);
fprintf("rxGrid20 size: %s\n", mat2str(size(rxGrid20)));
fprintf("Number of OFDM symbols: %d\n", nSymbols);

clear correctedWaveformAligned;

%% ------------------------------------------------------------------------
% PSS correlation per OFDM symbol
% -------------------------------------------------------------------------

fprintf("\n=== Computing PSS correlation per OFDM symbol ===\n");

pssRows = nrPSSIndices;
pssRef = nrPSS(syncState.NID2);
pssRef = pssRef(:);

pssMetric = nan(nSymbols, 1);

t0 = tic;

for symbolIdx = 1:nSymbols
    y = rxGrid20(pssRows, symbolIdx);
    y = y(:);

    pssMetric(symbolIdx) = abs(pssRef' * y) / (norm(pssRef) * norm(y) + eps);
end

tPSS = toc(t0);

fprintf("PSS correlation time: %.3f s\n", tPSS);
fprintf("PSS metric: median=%.3f | max=%.3f\n", median(pssMetric, "omitnan"), max(pssMetric));

%% ------------------------------------------------------------------------
% Peak detection
% -------------------------------------------------------------------------

metricMedian = median(pssMetric, "omitnan");
metricRobustSigma = 1.4826 * median(abs(pssMetric - metricMedian), "omitnan");

adaptiveThreshold = metricMedian + cfg.AdaptiveThresholdSigma * metricRobustSigma;
adaptiveThreshold = min(adaptiveThreshold, cfg.MaxAdaptiveThreshold);

detectionThreshold = max(cfg.PSSCorrThreshold, adaptiveThreshold);

fprintf("\n=== Detecting PSS peaks ===\n");
fprintf("Fixed threshold:       %.3f\n", cfg.PSSCorrThreshold);
fprintf("Adaptive threshold:    %.3f\n", adaptiveThreshold);
fprintf("Detection threshold:   %.3f\n", detectionThreshold);

localMax = false(nSymbols, 1);

if nSymbols >= 3
    localMax(2:end-1) = ...
        pssMetric(2:end-1) >= pssMetric(1:end-2) & ...
        pssMetric(2:end-1) >  pssMetric(3:end);
end

if nSymbols >= 2
    localMax(1) = pssMetric(1) > pssMetric(2);
    localMax(end) = pssMetric(end) > pssMetric(end-1);
end

candidateSymbols = find(localMax & pssMetric >= detectionThreshold);

fprintf("Candidate peaks before NMS: %d\n", numel(candidateSymbols));

detectedSymbols = nonMaximumSuppressSymbols( ...
    candidateSymbols, ...
    pssMetric, ...
    cfg.MinPeakDistanceSymbols);

detectedSymbols = sort(detectedSymbols(:));
numDetected = numel(detectedSymbols);

fprintf("Detected PSS/SSB arrivals after NMS: %d\n", numDetected);

if numDetected == 0
    warning("No PSS/SSB arrivals detected. Try lowering PSS_CORR_THRESHOLD, for example 0.60.");
end

%% ------------------------------------------------------------------------
% Convert detected symbols to sample/time
% -------------------------------------------------------------------------

symbolLengthsPattern = double(ofdmInfo.SymbolLengths(:));

if isempty(symbolLengthsPattern)
    error("ofdmInfo.SymbolLengths is empty. Cannot compute symbol timestamps.");
end

symbolLengths = repmat(symbolLengthsPattern, ceil(nSymbols / numel(symbolLengthsPattern)), 1);
symbolLengths = symbolLengths(1:nSymbols);

symbolStartSamplesAligned = cumsum([0; symbolLengths(1:end-1)]);

detectedSymbolStartSamplesAligned = symbolStartSamplesAligned(detectedSymbols);

detectedSampleIndexFromCaptureStart = ...
    initialTimingOffset + detectedSymbolStartSamplesAligned;

detectedRelativeTimeSeconds = detectedSampleIndexFromCaptureStart / cfg.SampleRate;
detectedUnixTime = captureStartUnixTime + detectedRelativeTimeSeconds;

interArrivalSeconds = nan(numDetected, 1);

if numDetected >= 2
    interArrivalSeconds(2:end) = diff(detectedUnixTime);
end

pssMetricDetected = pssMetric(detectedSymbols);

pssDateTimeUTC = datetime(detectedUnixTime, ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "UTC", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

pssDateTimeMadrid = pssDateTimeUTC;
pssDateTimeMadrid.TimeZone = "Europe/Madrid";
pssDateTimeMadrid.Format = "yyyy-MM-dd HH:mm:ss.SSSSSS";

%% ------------------------------------------------------------------------
% Output table
% -------------------------------------------------------------------------

T = table();

T.arrival_index = (1:numDetected).';
T.symbol_index = detectedSymbols(:);
T.sample_index_from_capture_start = detectedSampleIndexFromCaptureStart(:);
T.pss_time_madrid = string(pssDateTimeMadrid(:));
T.pss_time_utc = string(pssDateTimeUTC(:));
T.pss_unix_time = detectedUnixTime(:);
T.pss_relative_time_s = detectedRelativeTimeSeconds(:);
T.inter_arrival_s = interArrivalSeconds(:);
T.pss_metric = pssMetricDetected(:);

fprintf("\n=== First detected physical SSB arrivals ===\n");

nPrint = min(50, height(T));

if nPrint > 0
    disp(T(1:nPrint, :));
end

fprintf("\n=== Physical SSB inter-arrival summary ===\n");

validInter = interArrivalSeconds(~isnan(interArrivalSeconds));

if isempty(validInter)
    fprintf("Not enough detected arrivals to compute inter-arrival statistics.\n");
else
    fprintf("N intervals: %d\n", numel(validInter));
    fprintf("Mean:        %.6f s\n", mean(validInter, "omitnan"));
    fprintf("Median:      %.6f s\n", median(validInter, "omitnan"));
    fprintf("P05:         %.6f s\n", prctile(validInter, 5));
    fprintf("P95:         %.6f s\n", prctile(validInter, 95));
    fprintf("Min:         %.6f s\n", min(validInter));
    fprintf("Max:         %.6f s\n", max(validInter));

    fprintf("\nRounded intervals in milliseconds:\n");
    roundedMs = round(validInter * 1000, 3);
    disp(tabulate(round(roundedMs, 1)));
end

%% ------------------------------------------------------------------------
% Save results
% -------------------------------------------------------------------------

results = struct();
results.cfg = cfg;
results.syncState = syncState;
results.captureStartUnixTime = captureStartUnixTime;
results.captureEndUnixTime = captureEndUnixTime;
results.captureElapsedSeconds = captureElapsedSeconds;
results.captureStartDateTimeUTC = string(captureStartDateTimeUTC);
results.captureEndDateTimeUTC = string(captureEndDateTimeUTC);
results.initialTimingOffset = initialTimingOffset;
results.initialTimingOffsetSeconds = initialTimingOffset / cfg.SampleRate;
results.tFreqApply = tFreqApply;
results.tInitialTiming = tInitialTiming;
results.tDemod = tDemod;
results.tPSS = tPSS;
results.pssMetric = pssMetric;
results.detectionThreshold = detectionThreshold;
results.candidateSymbols = candidateSymbols;
results.detectedSymbols = detectedSymbols;
results.detectedUnixTime = detectedUnixTime;
results.detectedRelativeTimeSeconds = detectedRelativeTimeSeconds;
results.interArrivalSeconds = interArrivalSeconds;
results.pssMetricDetected = pssMetricDetected;
results.table = T;

save(cfg.OutputMatFile, ...
    "results", ...
    "cfg", ...
    "syncState", ...
    "pssMetric", ...
    "detectedSymbols", ...
    "detectedUnixTime", ...
    "detectedRelativeTimeSeconds", ...
    "interArrivalSeconds", ...
    "T", ...
    "-v7.3");

writetable(T, cfg.OutputCsvFile);

fprintf("\nSaved MAT file:\n%s\n", cfg.OutputMatFile);
fprintf("Saved CSV file:\n%s\n", cfg.OutputCsvFile);

release(rx);

%% ------------------------------------------------------------------------
% Local functions
% -------------------------------------------------------------------------

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


function y = applyFrequencyCorrection(x, freqOffsetHz, sampleRate, correctionSign)
    n = (0:size(x, 1)-1).';
    rot = exp(1j * correctionSign * 2 * pi * freqOffsetHz * n / sampleRate);
    y = x .* rot;
end


function selectedSymbols = nonMaximumSuppressSymbols(candidateSymbols, metric, minDistanceSymbols)
    selectedSymbols = [];

    if isempty(candidateSymbols)
        return;
    end

    [~, order] = sort(metric(candidateSymbols), "descend");
    candidatesSorted = candidateSymbols(order);

    for k = 1:numel(candidatesSorted)
        candidate = candidatesSorted(k);

        if isempty(selectedSymbols)
            selectedSymbols(end + 1, 1) = candidate; %#ok<AGROW>
        else
            if all(abs(candidate - selectedSymbols) >= minDistanceSymbols)
                selectedSymbols(end + 1, 1) = candidate; %#ok<AGROW>
            end
        end
    end

    selectedSymbols = sort(selectedSymbols(:));
end


function safeRelease(rx)
    try
        release(rx);
    catch
    end
end
