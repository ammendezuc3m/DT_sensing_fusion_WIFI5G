%% Live rxGridSSB motion / Doppler-like viewer
%
% Goal:
%   Visualize whether human movement produces measurable temporal variation
%   in the 5G SSB rxGridSSB.
%
% This script:
%   - Captures SSBs using the validated fast pipeline:
%       fixed frequency offset + dynamic timing per capture.
%   - Extracts:
%       rxGridSSB = rxGridSave(61:300, 2:5)
%   - Builds slow-time series:
%       X(t,k) = mean over OFDM symbols of rxGridSSB(k,:)
%   - Computes:
%       1) power baseline delta
%       2) power temporal delta
%       3) complex temporal delta
%       4) phase jitter after common phase removal
%       5) Doppler-like spectrum over slow-time
%       6) Doppler energy ratio
%       7) Doppler peak and centroid
%
% Recommended experiment:
%   For every run, stay still during the baseline period.
%   Then perform the requested movement.
%
% Environment variables:
%   MOTION_LABEL          e.g. far_slow, far_fast, near_slow, near_fast, near_static_P5
%   MAX_VALID_SSB         default 700
%   BASELINE_SSB          default 60
%   DOPPLER_WINDOW        default 64
%   SHOW_FIGURE           default 1
%   UPDATE_EVERY          default 5
%   RADIO_GAIN            default 70
%   RESYNC_EVERY_N        default 2000
%
% Output:
%   results/motion_doppler/<label>_<timestamp>/
%       motion_log.csv
%       motion_session.mat
%       motion_live_final.png
%       summary.txt

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

cfg.Label = string(getenvDefault("MOTION_LABEL", "motion_test"));
cfg.Timestamp = string(datestr(now, "yyyymmdd_HHMMSS"));

cfg.MaxValidSSB = round(str2double(getenvDefault("MAX_VALID_SSB", "700")));
cfg.BaselineSSB = round(str2double(getenvDefault("BASELINE_SSB", "60")));

cfg.BufferN = round(str2double(getenvDefault("BUFFER_N", "160")));
cfg.DopplerWindow = round(str2double(getenvDefault("DOPPLER_WINDOW", "64")));
cfg.MinDopplerSamples = round(str2double(getenvDefault("MIN_DOPPLER_SAMPLES", "24")));

cfg.ShowFigure = str2double(getenvDefault("SHOW_FIGURE", "1")) ~= 0;
cfg.UpdateEvery = round(str2double(getenvDefault("UPDATE_EVERY", "5")));

cfg.DcRejectHz = str2double(getenvDefault("DC_REJECT_HZ", "0.35"));
cfg.FastBandHz = str2double(getenvDefault("FAST_BAND_HZ", "2.0"));
cfg.MaxDopplerHz = str2double(getenvDefault("MAX_DOPPLER_HZ", "8.0"));

cfg.NumWarmupCaptures = round(str2double(getenvDefault("WARMUP_CAPTURES", "30")));
cfg.MinValidWarmupCaptures = 10;

cfg.FrequencyCorrectionSign = str2double(getenvDefault("FREQ_CORRECTION_SIGN", "-1"));

cfg.ResyncEveryNCaptures = round(str2double(getenvDefault("RESYNC_EVERY_N", "2000")));
cfg.ResyncCaptures = round(str2double(getenvDefault("RESYNC_CAPTURES", "8")));

cfg.RadioOptionIndex = 10;
cfg.AntennaOptionIndex = 1;
cfg.RadioGain = str2double(getenvDefault("RADIO_GAIN", "70"));

cfg.Band = "n78";
cfg.GSCN = 7875;
cfg.UseCustomCenterFrequency = false;
cfg.CustomCenterFrequencyHz = 3541.44e6;

cfg.NRBSSB = 20;
cfg.DemodRB = 30;
cfg.NSlot = 0;

cfg.FramesPerCapture = 1;
cfg.CaptureDurationSeconds = (cfg.FramesPerCapture + 1) * 10e-3;

cfg.SSBRows = 61:300;
cfg.SSBCols = 2:5;

cfg.ProgressEveryValid = 25;

outDir = fullfile(projectRoot, "results", "motion_doppler", cfg.Label + "_" + cfg.Timestamp);
if ~exist(outDir, "dir")
    mkdir(outDir);
end

csvPath = fullfile(outDir, "motion_log.csv");
matPath = fullfile(outDir, "motion_session.mat");
figPath = fullfile(outDir, "motion_live_final.png");
summaryPath = fullfile(outDir, "summary.txt");

fprintf("\n=== Motion / Doppler-like session ===\n");
fprintf("Label:            %s\n", cfg.Label);
fprintf("Output directory: %s\n", outDir);
fprintf("Max valid SSB:    %d\n", cfg.MaxValidSSB);
fprintf("Baseline SSB:     %d\n", cfg.BaselineSSB);
fprintf("Doppler window:   %d\n", cfg.DopplerWindow);
fprintf("Show figure:      %d\n", cfg.ShowFigure);
fprintf("\nIMPORTANT: stay still during the first %d valid SSBs for baseline.\n\n", cfg.BaselineSSB);

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

fprintf("Center frequency: %.3f MHz\n", cfg.CenterFrequency / 1e6);
fprintf("Sample rate:      %.3f Msps\n", cfg.SampleRate / 1e6);
fprintf("SCS:              %.0f kHz\n", cfg.SCSNumeric);
fprintf("Capture duration: %.3f ms\n", cfg.CaptureDurationSeconds * 1000);

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
% Preallocate logs
% -------------------------------------------------------------------------

N = cfg.MaxValidSSB;

rxGridLog = complex(zeros(240, 4, N, "single"));
complexVecLog = complex(zeros(240, N, "single"));
powerDbLog = zeros(240, N, "single");
deltaPowerSubcarrierDbLog = zeros(240, N, "single");

timeLog = nan(N, 1);
captureTimeLog = nan(N, 1);
processTimeLog = nan(N, 1);
totalTimeLog = nan(N, 1);
timingOffsetLog = nan(N, 1);

motionBaselinePowerDbLog = nan(N, 1);
motionTemporalPowerDbLog = nan(N, 1);
motionBaselineComplexLog = nan(N, 1);
motionTemporalComplexLog = nan(N, 1);
phaseJitterLog = nan(N, 1);

dopplerEnergyRatioLog = nan(N, 1);
dopplerFastEnergyRatioLog = nan(N, 1);
dopplerPeakHzLog = nan(N, 1);
dopplerCentroidHzLog = nan(N, 1);
slowFsHzLog = nan(N, 1);

validCount = 0;
failedCount = 0;

baselinePowerSum = zeros(240, 1);
baselineComplexSum = complex(zeros(240, 1));
baselineReady = false;
baselinePowerDb = [];
baselineComplex = [];

prevPowerDb = [];
prevComplexVec = [];

%% ------------------------------------------------------------------------
% CSV log
% -------------------------------------------------------------------------

csvFile = fopen(csvPath, "w");
fprintf(csvFile, "idx,time_s,capture_ms,process_ms,total_ms,timing_offset,");
fprintf(csvFile, "baseline_power_db_delta,temporal_power_db_delta,baseline_complex_delta,temporal_complex_delta,phase_jitter_rad,");
fprintf(csvFile, "doppler_energy_ratio,doppler_fast_energy_ratio,doppler_peak_hz,doppler_centroid_hz,slow_fs_hz,failed_count\n");

%% ------------------------------------------------------------------------
% Figure setup
% -------------------------------------------------------------------------

if cfg.ShowFigure
    fig = figure("Name", "Live rxGridSSB motion / Doppler-like viewer", "Color", "w");
else
    fig = figure("Name", "Live rxGridSSB motion / Doppler-like viewer", "Color", "w", "Visible", "off");
end

tl = tiledlayout(fig, 2, 2, "TileSpacing", "compact", "Padding", "compact");

ax1 = nexttile(tl, 1);
hold(ax1, "on");
grid(ax1, "on");
title(ax1, "Motion scores");
xlabel(ax1, "Time [s]");
ylabel(ax1, "Score");
motionLine1 = plot(ax1, nan, nan, "LineWidth", 1.5, "DisplayName", "Temporal power Δ [dB]");
motionLine2 = plot(ax1, nan, nan, "LineWidth", 1.5, "DisplayName", "Doppler energy ratio");
motionLine3 = plot(ax1, nan, nan, "LineWidth", 1.5, "DisplayName", "Fast-band ratio");
legend(ax1, "Location", "best");

ax2 = nexttile(tl, 2);
title(ax2, "Δ power by subcarrier over time");
xlabel(ax2, "Time [s]");
ylabel(ax2, "SSB subcarrier");
heatImg = imagesc(ax2, nan(240, 10));
axis(ax2, "xy");
colorbar(ax2);

ax3 = nexttile(tl, 3);
hold(ax3, "on");
grid(ax3, "on");
title(ax3, "Current Doppler-like spectrum");
xlabel(ax3, "Frequency [Hz]");
ylabel(ax3, "Normalized power");
dopplerLine = plot(ax3, nan, nan, "LineWidth", 1.5);
xline(ax3, 0, "--");
xlim(ax3, [-8 8]);

ax4 = nexttile(tl, 4);
hold(ax4, "on");
grid(ax4, "on");
title(ax4, "Current power per subcarrier");
xlabel(ax4, "SSB subcarrier");
ylabel(ax4, "Power [dB]");
currentPowerLine = plot(ax4, nan, nan, "LineWidth", 1.2, "DisplayName", "current");
baselinePowerLine = plot(ax4, nan, nan, "--", "LineWidth", 1.2, "DisplayName", "baseline");
legend(ax4, "Location", "best");

drawnow;

%% ------------------------------------------------------------------------
% Main loop
% -------------------------------------------------------------------------

fprintf("\n=== Starting motion / Doppler-like capture ===\n");
fprintf("Stay still until baseline is ready.\n\n");

tExperiment = tic;

while validCount < cfg.MaxValidSSB

    tLoop = tic;

    try
        tCapture = tic;
        waveform = capture(rx, captureDuration);
        captureTime = toc(tCapture);

        tProcess = tic;
        result = processOneWaveformToRxGridSSB(waveform, cfg, syncState);
        processTime = toc(tProcess);

        if ~result.Success
            failedCount = failedCount + 1;
            fprintf("Failed capture: %s\n", result.ErrorMessage);
            continue;
        end

        validCount = validCount + 1;
        timeNow = toc(tExperiment);

        rxGridSSB = result.RxGridSSB;

        complexVec = mean(rxGridSSB, 2);
        powerPerSubcarrier = mean(abs(rxGridSSB).^2, 2);
        powerDb = 10 * log10(double(powerPerSubcarrier) + eps);

        rxGridLog(:, :, validCount) = single(rxGridSSB);
        complexVecLog(:, validCount) = single(complexVec);
        powerDbLog(:, validCount) = single(powerDb);

        timeLog(validCount) = timeNow;
        captureTimeLog(validCount) = captureTime;
        processTimeLog(validCount) = processTime;
        timingOffsetLog(validCount) = result.TimingOffsetUsed;

        if validCount <= cfg.BaselineSSB
            baselinePowerSum = baselinePowerSum + double(powerDb);
            baselineComplexSum = baselineComplexSum + double(complexVec);

            if validCount == cfg.BaselineSSB
                baselinePowerDb = baselinePowerSum / cfg.BaselineSSB;
                baselineComplex = baselineComplexSum / cfg.BaselineSSB;
                baselineReady = true;

                fprintf("\nBASELINE READY at valid SSB %d. Start movement now if this is a moving session.\n\n", validCount);
            end
        end

        if baselineReady
            motionBaselinePowerDb = mean(abs(powerDb - baselinePowerDb));
            complexAlignedToBaseline = alignCommonPhase(complexVec, baselineComplex);
            motionBaselineComplex = mean(abs(complexAlignedToBaseline - baselineComplex).^2) / ...
                (mean(abs(baselineComplex).^2) + eps);
        else
            motionBaselinePowerDb = NaN;
            motionBaselineComplex = NaN;
        end

        if ~isempty(prevPowerDb)
            deltaPowerSubcarrier = abs(powerDb - prevPowerDb);
            motionTemporalPowerDb = mean(deltaPowerSubcarrier);

            complexAlignedToPrev = alignCommonPhase(complexVec, prevComplexVec);
            motionTemporalComplex = mean(abs(complexAlignedToPrev - prevComplexVec).^2) / ...
                (mean(abs(prevComplexVec).^2) + eps);

            phaseDiff = angle(complexVec .* conj(prevComplexVec));
            commonPhase = angle(mean(exp(1j * phaseDiff)));
            phaseDiffCorr = angle(exp(1j * (phaseDiff - commonPhase)));
            phaseJitter = median(abs(phaseDiffCorr));

            deltaPowerSubcarrierDbLog(:, validCount) = single(deltaPowerSubcarrier);
        else
            motionTemporalPowerDb = NaN;
            motionTemporalComplex = NaN;
            phaseJitter = NaN;
        end

        prevPowerDb = powerDb;
        prevComplexVec = complexVec;

        dop = computeDopplerLike(complexVecLog, timeLog, validCount, cfg);

        motionBaselinePowerDbLog(validCount) = motionBaselinePowerDb;
        motionTemporalPowerDbLog(validCount) = motionTemporalPowerDb;
        motionBaselineComplexLog(validCount) = motionBaselineComplex;
        motionTemporalComplexLog(validCount) = motionTemporalComplex;
        phaseJitterLog(validCount) = phaseJitter;

        dopplerEnergyRatioLog(validCount) = dop.energyRatio;
        dopplerFastEnergyRatioLog(validCount) = dop.fastEnergyRatio;
        dopplerPeakHzLog(validCount) = dop.peakHz;
        dopplerCentroidHzLog(validCount) = dop.centroidHz;
        slowFsHzLog(validCount) = dop.fsSlowHz;

        totalTime = toc(tLoop);
        totalTimeLog(validCount) = totalTime;

        fprintf(csvFile, "%d,%.6f,%.6f,%.6f,%.6f,%.0f,", ...
            validCount, timeNow, captureTime * 1000, processTime * 1000, totalTime * 1000, result.TimingOffsetUsed);

        fprintf(csvFile, "%.8f,%.8f,%.8f,%.8f,%.8f,", ...
            motionBaselinePowerDb, motionTemporalPowerDb, motionBaselineComplex, motionTemporalComplex, phaseJitter);

        fprintf(csvFile, "%.8f,%.8f,%.8f,%.8f,%.8f,%d\n", ...
            dop.energyRatio, dop.fastEnergyRatio, dop.peakHz, dop.centroidHz, dop.fsSlowHz, failedCount);

        if mod(validCount, cfg.ProgressEveryValid) == 0 || validCount <= cfg.BaselineSSB
            elapsed = toc(tExperiment);

            if baselineReady
                phaseTxt = "MEASURE";
            else
                phaseTxt = "BASELINE";
            end

            fprintf("[%s] %4d/%4d | rate=%.2f/s | tempPowerΔ=%.4f dB | dopRatio=%.4f | fastRatio=%.4f | peak=%.2f Hz | centroid=%.2f Hz | total=%.2f ms\n", ...
                phaseTxt, ...
                validCount, cfg.MaxValidSSB, validCount / elapsed, ...
                motionTemporalPowerDb, dop.energyRatio, dop.fastEnergyRatio, dop.peakHz, dop.centroidHz, totalTime * 1000);
        end

        if mod(validCount, cfg.UpdateEvery) == 0 || validCount == cfg.BaselineSSB
            updateLiveFigure( ...
                fig, cfg, validCount, ...
                timeLog, powerDbLog, deltaPowerSubcarrierDbLog, ...
                baselineReady, baselinePowerDb, ...
                motionTemporalPowerDbLog, dopplerEnergyRatioLog, dopplerFastEnergyRatioLog, ...
                dop, ...
                motionLine1, motionLine2, motionLine3, heatImg, dopplerLine, currentPowerLine, baselinePowerLine, ...
                ax1, ax2, ax3, ax4);
        end

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

    catch ME
        failedCount = failedCount + 1;
        fprintf("Loop error: %s\n", ME.message);
    end
end

fprintf("\nCapture finished.\n");

fclose(csvFile);

%% ------------------------------------------------------------------------
% Trim logs and save
% -------------------------------------------------------------------------

idx = 1:validCount;

rxGridLog = rxGridLog(:, :, idx);
complexVecLog = complexVecLog(:, idx);
powerDbLog = powerDbLog(:, idx);
deltaPowerSubcarrierDbLog = deltaPowerSubcarrierDbLog(:, idx);

timeLog = timeLog(idx);
captureTimeLog = captureTimeLog(idx);
processTimeLog = processTimeLog(idx);
totalTimeLog = totalTimeLog(idx);
timingOffsetLog = timingOffsetLog(idx);

motionBaselinePowerDbLog = motionBaselinePowerDbLog(idx);
motionTemporalPowerDbLog = motionTemporalPowerDbLog(idx);
motionBaselineComplexLog = motionBaselineComplexLog(idx);
motionTemporalComplexLog = motionTemporalComplexLog(idx);
phaseJitterLog = phaseJitterLog(idx);

dopplerEnergyRatioLog = dopplerEnergyRatioLog(idx);
dopplerFastEnergyRatioLog = dopplerFastEnergyRatioLog(idx);
dopplerPeakHzLog = dopplerPeakHzLog(idx);
dopplerCentroidHzLog = dopplerCentroidHzLog(idx);
slowFsHzLog = slowFsHzLog(idx);

saveas(fig, figPath);

summary = computeSessionSummary( ...
    cfg, validCount, failedCount, ...
    timeLog, ...
    motionBaselinePowerDbLog, motionTemporalPowerDbLog, motionBaselineComplexLog, motionTemporalComplexLog, phaseJitterLog, ...
    dopplerEnergyRatioLog, dopplerFastEnergyRatioLog, dopplerPeakHzLog, dopplerCentroidHzLog, slowFsHzLog);

writeSummary(summaryPath, summary);

save(matPath, ...
    "cfg", "syncState", "summary", ...
    "rxGridLog", "complexVecLog", "powerDbLog", "deltaPowerSubcarrierDbLog", ...
    "timeLog", "captureTimeLog", "processTimeLog", "totalTimeLog", "timingOffsetLog", ...
    "motionBaselinePowerDbLog", "motionTemporalPowerDbLog", ...
    "motionBaselineComplexLog", "motionTemporalComplexLog", "phaseJitterLog", ...
    "dopplerEnergyRatioLog", "dopplerFastEnergyRatioLog", "dopplerPeakHzLog", "dopplerCentroidHzLog", "slowFsHzLog", ...
    "baselineReady", "baselinePowerDb", "baselineComplex", ...
    "-v7.3");

fprintf("\nSaved outputs:\n");
fprintf("  CSV:     %s\n", csvPath);
fprintf("  MAT:     %s\n", matPath);
fprintf("  Figure:  %s\n", figPath);
fprintf("  Summary: %s\n", summaryPath);

fprintf("\n=== Summary ===\n");
fprintf("%s\n", summary);

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


function aligned = alignCommonPhase(x, ref)
    x = double(x);
    ref = double(ref);

    alpha = sum(x .* conj(ref)) / (sum(abs(ref).^2) + eps);
    phi = angle(alpha);

    aligned = x * exp(-1j * phi);
end


function dop = computeDopplerLike(complexVecLog, timeLog, validCount, cfg)
    dop = struct();
    dop.freqHz = NaN;
    dop.spectrum = NaN;
    dop.energyRatio = NaN;
    dop.fastEnergyRatio = NaN;
    dop.peakHz = NaN;
    dop.centroidHz = NaN;
    dop.fsSlowHz = NaN;

    M = min(cfg.DopplerWindow, validCount);

    if M < cfg.MinDopplerSamples
        return;
    end

    idx = validCount - M + 1:validCount;

    t = timeLog(idx);
    if any(isnan(t)) || numel(t) < 3
        return;
    end

    dt = median(diff(t));
    if ~isfinite(dt) || dt <= 0
        return;
    end

    fsSlow = 1 / dt;
    dop.fsSlowHz = fsSlow;

    X = double(complexVecLog(:, idx)); % [240, M]

    ref = mean(X, 2);

    for m = 1:M
        X(:, m) = alignCommonPhase(X(:, m), ref);
    end

    X = X - mean(X, 2);

    if M == 1
        return;
    end

    n = 0:M-1;
    win = 0.5 - 0.5 * cos(2 * pi * n / max(M - 1, 1));
    Xw = X .* win;

    S = fftshift(fft(Xw, [], 2), 2);
    spectrum = mean(abs(S).^2, 1);

    freqHz = ((0:M-1) - floor(M/2)) * fsSlow / M;

    totalEnergy = sum(spectrum) + eps;

    validBand = abs(freqHz) > cfg.DcRejectHz & abs(freqHz) <= min(cfg.MaxDopplerHz, fsSlow / 2);
    fastBand = abs(freqHz) >= cfg.FastBandHz & abs(freqHz) <= min(cfg.MaxDopplerHz, fsSlow / 2);

    dop.freqHz = freqHz;
    dop.spectrum = spectrum / max(spectrum + eps);

    if any(validBand)
        bandSpectrum = spectrum(validBand);
        bandFreq = freqHz(validBand);

        dop.energyRatio = sum(bandSpectrum) / totalEnergy;

        [~, maxIdx] = max(bandSpectrum);
        dop.peakHz = bandFreq(maxIdx);

        dop.centroidHz = sum(abs(bandFreq) .* bandSpectrum) / (sum(bandSpectrum) + eps);
    end

    if any(fastBand)
        dop.fastEnergyRatio = sum(spectrum(fastBand)) / totalEnergy;
    end
end


function updateLiveFigure( ...
    fig, cfg, validCount, ...
    timeLog, powerDbLog, deltaPowerSubcarrierDbLog, ...
    baselineReady, baselinePowerDb, ...
    motionTemporalPowerDbLog, dopplerEnergyRatioLog, dopplerFastEnergyRatioLog, ...
    dop, ...
    motionLine1, motionLine2, motionLine3, heatImg, dopplerLine, currentPowerLine, baselinePowerLine, ...
    ax1, ax2, ax3, ax4)

    idxEnd = validCount;
    idxStart = max(1, idxEnd - cfg.BufferN + 1);
    idx = idxStart:idxEnd;

    t = timeLog(idx);
    t = t - t(1);

    set(motionLine1, "XData", t, "YData", motionTemporalPowerDbLog(idx));
    set(motionLine2, "XData", t, "YData", dopplerEnergyRatioLog(idx));
    set(motionLine3, "XData", t, "YData", dopplerFastEnergyRatioLog(idx));

    title(ax1, sprintf("Motion scores | label=%s | SSB=%d", cfg.Label, validCount));

    heatData = deltaPowerSubcarrierDbLog(:, idx);
    set(heatImg, "XData", t, "YData", 1:240, "CData", heatData);
    title(ax2, "Δ power by subcarrier over time [dB]");
    xlabel(ax2, sprintf("Time window [s], last %.1f s", t(end)));
    ylabel(ax2, "SSB subcarrier");
    axis(ax2, "xy");

    if ~all(isnan(dop.freqHz))
        set(dopplerLine, "XData", dop.freqHz, "YData", dop.spectrum);
        xlim(ax3, [-min(cfg.MaxDopplerHz, dop.fsSlowHz/2), min(cfg.MaxDopplerHz, dop.fsSlowHz/2)]);
        title(ax3, sprintf("Doppler-like spectrum | ratio=%.3f | peak=%.2f Hz | centroid=%.2f Hz", ...
            dop.energyRatio, dop.peakHz, dop.centroidHz));
    end

    set(currentPowerLine, "XData", 1:240, "YData", double(powerDbLog(:, validCount)));

    if baselineReady
        set(baselinePowerLine, "XData", 1:240, "YData", baselinePowerDb);
    end

    title(ax4, "Current power per subcarrier");
    xlabel(ax4, "SSB subcarrier");
    ylabel(ax4, "Power [dB]");

    drawnow limitrate;
end


function summary = computeSessionSummary( ...
    cfg, validCount, failedCount, ...
    timeLog, ...
    motionBaselinePowerDbLog, motionTemporalPowerDbLog, motionBaselineComplexLog, motionTemporalComplexLog, phaseJitterLog, ...
    dopplerEnergyRatioLog, dopplerFastEnergyRatioLog, dopplerPeakHzLog, dopplerCentroidHzLog, slowFsHzLog)

    measureIdx = cfg.BaselineSSB + 1:validCount;
    measureIdx = measureIdx(measureIdx >= 1 & measureIdx <= validCount);

    if isempty(measureIdx)
        measureIdx = 1:validCount;
    end

    duration = timeLog(validCount) - timeLog(1);

    txt = "";
    txt = txt + sprintf("Label: %s\n", cfg.Label);
    txt = txt + sprintf("Valid SSB: %d\n", validCount);
    txt = txt + sprintf("Failed captures: %d\n", failedCount);
    txt = txt + sprintf("Duration: %.3f s\n", duration);
    txt = txt + sprintf("Average valid rate: %.3f SSB/s\n", validCount / max(duration, eps));
    txt = txt + sprintf("Baseline SSB: %d\n", cfg.BaselineSSB);
    txt = txt + sprintf("Doppler window: %d\n", cfg.DopplerWindow);
    txt = txt + sprintf("Median slow Fs: %.3f Hz\n", median(slowFsHzLog(measureIdx), "omitnan"));
    txt = txt + newline;

    txt = txt + sprintf("Measurement region statistics, after baseline:\n");
    txt = txt + sprintf("  baseline_power_delta_db median: %.6f\n", median(motionBaselinePowerDbLog(measureIdx), "omitnan"));
    txt = txt + sprintf("  baseline_power_delta_db p90:    %.6f\n", prctile(motionBaselinePowerDbLog(measureIdx), 90));
    txt = txt + sprintf("  temporal_power_delta_db median: %.6f\n", median(motionTemporalPowerDbLog(measureIdx), "omitnan"));
    txt = txt + sprintf("  temporal_power_delta_db p90:    %.6f\n", prctile(motionTemporalPowerDbLog(measureIdx), 90));
    txt = txt + sprintf("  baseline_complex_delta median:  %.6f\n", median(motionBaselineComplexLog(measureIdx), "omitnan"));
    txt = txt + sprintf("  temporal_complex_delta median:  %.6f\n", median(motionTemporalComplexLog(measureIdx), "omitnan"));
    txt = txt + sprintf("  phase_jitter_rad median:        %.6f\n", median(phaseJitterLog(measureIdx), "omitnan"));
    txt = txt + newline;

    txt = txt + sprintf("Doppler-like statistics, after baseline:\n");
    txt = txt + sprintf("  doppler_energy_ratio median:      %.6f\n", median(dopplerEnergyRatioLog(measureIdx), "omitnan"));
    txt = txt + sprintf("  doppler_energy_ratio p90:         %.6f\n", prctile(dopplerEnergyRatioLog(measureIdx), 90));
    txt = txt + sprintf("  doppler_fast_energy_ratio median: %.6f\n", median(dopplerFastEnergyRatioLog(measureIdx), "omitnan"));
    txt = txt + sprintf("  doppler_fast_energy_ratio p90:    %.6f\n", prctile(dopplerFastEnergyRatioLog(measureIdx), 90));
    txt = txt + sprintf("  abs(doppler_peak_hz) median:      %.6f Hz\n", median(abs(dopplerPeakHzLog(measureIdx)), "omitnan"));
    txt = txt + sprintf("  doppler_centroid_hz median:       %.6f Hz\n", median(dopplerCentroidHzLog(measureIdx), "omitnan"));

    summary = txt;
end


function writeSummary(path, summary)
    fid = fopen(path, "w");
    fprintf(fid, "%s", summary);
    fclose(fid);
end


function safeCleanup(rx)
    try
        release(rx);
    catch
    end
end
