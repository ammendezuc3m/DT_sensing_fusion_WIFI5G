%% Long-capture SSB slow-time motion / Doppler-like analysis
%
% Purpose:
%   Capture one continuous IQ block, extract consecutive physical SSBs using
%   one common frequency/timing reference, and analyze slow-time dynamics.
%
% Why this script exists:
%   The previous online viewer captured and synchronized each SSB independently.
%   That is useful for classification, but it can inject frame-to-frame jitter.
%   For Doppler/motion analysis we want a more coherent slow-time sequence.
%
% Experiment protocol:
%   - Stand still during the first BASELINE_SECONDS of the long capture.
%   - Then move if this is a moving session.
%   - If this is a static session, stay still for the whole capture.
%
% Environment variables:
%   MOTION_LABEL             default "long_motion_test"
%   LONG_CAPTURE_SECONDS     default 6
%   BASELINE_SECONDS         default 2
%   SHOW_FIGURE              default 1
%   RADIO_GAIN               default 70
%   FREQ_CORRECTION_SIGN     default -1
%   SAVE_RAW_WAVEFORM        default 0
%
% Outputs:
%   results/long_doppler/<label>_<timestamp>/
%       summary.txt
%       long_motion_log.csv
%       long_motion_session.mat
%       long_motion_final.png

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
fprintf("mySSBurstFrequencyCorrectFast location: %s\n", which("mySSBurstFrequencyCorrectFast"));

%% ------------------------------------------------------------------------
% Configuration
% -------------------------------------------------------------------------

cfg = struct();

cfg.Label = string(getenvDefault("MOTION_LABEL", "long_motion_test"));
cfg.Timestamp = string(datestr(now, "yyyymmdd_HHMMSS"));

cfg.LongCaptureSeconds = str2double(getenvDefault("LONG_CAPTURE_SECONDS", "6"));
cfg.BaselineSeconds = str2double(getenvDefault("BASELINE_SECONDS", "2"));

cfg.ShowFigure = str2double(getenvDefault("SHOW_FIGURE", "1")) ~= 0;
cfg.SaveRawWaveform = str2double(getenvDefault("SAVE_RAW_WAVEFORM", "0")) ~= 0;

cfg.RadioOptionIndex = 10;
cfg.AntennaOptionIndex = 1;
cfg.RadioGain = str2double(getenvDefault("RADIO_GAIN", "70"));

cfg.FrequencyCorrectionSign = str2double(getenvDefault("FREQ_CORRECTION_SIGN", "-1"));

cfg.Band = "n78";
cfg.GSCN = 7875;
cfg.UseCustomCenterFrequency = false;
cfg.CustomCenterFrequencyHz = 3541.44e6;

cfg.NRBSSB = 20;
cfg.DemodRB = 30;
cfg.NSlot = 0;

% Validated useful SSB region.
cfg.SSBRows = 61:300;
cfg.SSBCols = 2:5;

% Physical SSB periodicity observed previously.
cfg.SSBPeriodSeconds = 0.020;

% Segment used for OFDM demodulation starting at each expected SSB timing.
cfg.SegmentSeconds = 0.020;

% Doppler / motion bands.
cfg.DcRejectHz = 0.50;
cfg.LowBandMaxHz = 3.00;
cfg.MidBandMaxHz = 8.00;
cfg.HighBandMaxHz = 20.00;

% Sync chunk. 40 ms should include at least one physical SSB.
cfg.SyncChunkSeconds = 0.040;

outDir = fullfile(projectRoot, "results", "long_doppler", cfg.Label + "_" + cfg.Timestamp);
if ~exist(outDir, "dir")
    mkdir(outDir);
end

csvPath = fullfile(outDir, "long_motion_log.csv");
matPath = fullfile(outDir, "long_motion_session.mat");
figPath = fullfile(outDir, "long_motion_final.png");
summaryPath = fullfile(outDir, "summary.txt");

fprintf("\n=== Long-capture SSB motion / Doppler-like analysis ===\n");
fprintf("Label:                %s\n", cfg.Label);
fprintf("Output directory:     %s\n", outDir);
fprintf("Long capture seconds: %.3f s\n", cfg.LongCaptureSeconds);
fprintf("Baseline seconds:     %.3f s\n", cfg.BaselineSeconds);
fprintf("Show figure:          %d\n", cfg.ShowFigure);
fprintf("Save raw waveform:    %d\n", cfg.SaveRawWaveform);
fprintf("\nProtocol:\n");
fprintf("  1) During the first %.1f seconds of capture: stay still.\n", cfg.BaselineSeconds);
fprintf("  2) After that: move if this is a moving session.\n");
fprintf("  3) If static session: stay still all the time.\n\n");

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

fprintf("Center frequency: %.3f MHz\n", cfg.CenterFrequency / 1e6);
fprintf("Sample rate:      %.3f Msps\n", cfg.SampleRate / 1e6);
fprintf("SCS:              %.0f kHz\n", cfg.SCSNumeric);

estimatedSamples = round(cfg.LongCaptureSeconds * cfg.SampleRate);
estimatedMemoryGB = estimatedSamples * 16 / 1024^3; % complex double approx

fprintf("Estimated samples: %.0f\n", estimatedSamples);
fprintf("Approx waveform memory if complex double: %.2f GB\n", estimatedMemoryGB);

%% ------------------------------------------------------------------------
% Countdown and long capture
% -------------------------------------------------------------------------

fprintf("\nGet ready. Long capture starts after countdown.\n");
for k = 5:-1:1
    fprintf("%d...\n", k);
    pause(1);
end

fprintf("\nCAPTURING NOW. Stay still for first %.1f seconds.\n", cfg.BaselineSeconds);
fprintf("If moving session, start moving after that.\n\n");

tCapture = tic;
waveform = capture(rx, seconds(cfg.LongCaptureSeconds));
captureElapsed = toc(tCapture);

fprintf("Capture done in %.3f s.\n", captureElapsed);
fprintf("Waveform size: [%d x %d]\n", size(waveform, 1), size(waveform, 2));

release(rx);

%% ------------------------------------------------------------------------
% One common sync reference
% -------------------------------------------------------------------------

fprintf("\n=== Common synchronization reference ===\n");

syncChunkSamples = min(size(waveform, 1), round(cfg.SyncChunkSeconds * cfg.SampleRate));
syncChunk = waveform(1:syncChunkSamples, :);

[~, freqOffsetHz, NID2] = mySSBurstFrequencyCorrectFast( ...
    syncChunk, ...
    cfg.SSBBlockPattern, ...
    cfg.SampleRate, ...
    cfg.SearchBW, ...
    cfg.DisplayFigure);

fprintf("Estimated frequency offset from sync chunk: %.2f Hz\n", freqOffsetHz);
fprintf("Estimated NID2: %d\n", NID2);

correctedWaveform = applyFrequencyCorrection( ...
    waveform, ...
    freqOffsetHz, ...
    cfg.SampleRate, ...
    cfg.FrequencyCorrectionSign);

correctedSyncChunk = correctedWaveform(1:syncChunkSamples, :);
initialTimingOffset = estimateTimingOffset(correctedSyncChunk, NID2, cfg);

fprintf("Initial timing offset: %d samples\n", initialTimingOffset);

ssbPeriodSamples = round(cfg.SSBPeriodSeconds * cfg.SampleRate);
segmentSamples = round(cfg.SegmentSeconds * cfg.SampleRate);

fprintf("SSB period:       %.3f ms = %d samples\n", cfg.SSBPeriodSeconds * 1000, ssbPeriodSamples);
fprintf("Segment duration: %.3f ms = %d samples\n", cfg.SegmentSeconds * 1000, segmentSamples);

%% ------------------------------------------------------------------------
% Extract SSB sequence with fixed timing grid
% -------------------------------------------------------------------------

maxN = floor((size(correctedWaveform, 1) - initialTimingOffset - segmentSamples) / ssbPeriodSamples) + 1;
maxN = max(maxN, 0);

fprintf("\nExpected extractable SSBs: %d\n", maxN);

rxGridLog = complex(zeros(240, 4, maxN, "single"));
complexVecLog = complex(zeros(240, maxN, "single"));
powerDbLog = zeros(240, maxN, "single");
timeLog = nan(maxN, 1);
offsetLog = nan(maxN, 1);
successLog = false(maxN, 1);
errorLog = strings(maxN, 1);

validCount = 0;

for i = 1:maxN
    offset = initialTimingOffset + (i - 1) * ssbPeriodSamples;
    startIdx = 1 + offset;
    endIdx = startIdx + segmentSamples - 1;

    if startIdx < 1 || endIdx > size(correctedWaveform, 1)
        errorLog(i) = "segment_out_of_range";
        continue;
    end

    try
        segment = correctedWaveform(startIdx:endIdx, :);

        rxGridSave = nrOFDMDemodulate( ...
            segment, ...
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

        validCount = validCount + 1;

        rxGridLog(:, :, validCount) = single(rxGridSSB);
        complexVecLog(:, validCount) = single(mean(rxGridSSB, 2));

        powerPerSubcarrier = mean(abs(rxGridSSB).^2, 2);
        powerDbLog(:, validCount) = single(10 * log10(double(powerPerSubcarrier) + eps));

        timeLog(validCount) = offset / cfg.SampleRate;
        offsetLog(validCount) = offset;
        successLog(validCount) = true;

    catch ME
        errorLog(i) = string(ME.message);
    end
end

rxGridLog = rxGridLog(:, :, 1:validCount);
complexVecLog = complexVecLog(:, 1:validCount);
powerDbLog = powerDbLog(:, 1:validCount);
timeLog = timeLog(1:validCount);
offsetLog = offsetLog(1:validCount);

fprintf("Valid extracted SSBs: %d / %d\n", validCount, maxN);

if validCount < 20
    error("Too few valid SSBs extracted. Need to inspect timing/extraction.");
end

baselineN = max(3, min(validCount - 1, round(cfg.BaselineSeconds / cfg.SSBPeriodSeconds)));
cfg.BaselineN = baselineN;

fprintf("Baseline SSB count: %d\n", baselineN);

%% ------------------------------------------------------------------------
% Metrics
% -------------------------------------------------------------------------

powerMeanDb = mean(powerDbLog, 1).';
temporalPowerDeltaDb = nan(validCount, 1);
baselinePowerDeltaDb = nan(validCount, 1);

baselinePowerDb = mean(double(powerDbLog(:, 1:baselineN)), 2);
baselineComplex = mean(double(complexVecLog(:, 1:baselineN)), 2);

complexAlignedLog = complex(zeros(size(complexVecLog), "double"));
temporalComplexDelta = nan(validCount, 1);
phaseJitterRad = nan(validCount, 1);

for i = 1:validCount
    x = double(complexVecLog(:, i));
    xAligned = alignCommonPhase(x, baselineComplex);
    complexAlignedLog(:, i) = xAligned;

    p = double(powerDbLog(:, i));
    baselinePowerDeltaDb(i) = mean(abs(p - baselinePowerDb));

    if i > 1
        pPrev = double(powerDbLog(:, i-1));
        temporalPowerDeltaDb(i) = mean(abs(p - pPrev));

        xPrev = complexAlignedLog(:, i-1);
        temporalComplexDelta(i) = mean(abs(xAligned - xPrev).^2) / (mean(abs(xPrev).^2) + eps);

        phaseDiff = angle(xAligned .* conj(xPrev));
        commonPhase = angle(mean(exp(1j * phaseDiff)));
        phaseDiffCorr = angle(exp(1j * (phaseDiff - commonPhase)));
        phaseJitterRad(i) = median(abs(phaseDiffCorr));
    end
end

dop = computeDopplerFromSequence(complexAlignedLog, timeLog, baselineN, cfg);

%% ------------------------------------------------------------------------
% CSV log
% -------------------------------------------------------------------------

fid = fopen(csvPath, "w");
fprintf(fid, "idx,time_s,offset_samples,power_mean_db,baseline_power_db_delta,temporal_power_db_delta,temporal_complex_delta,phase_jitter_rad\n");

for i = 1:validCount
    fprintf(fid, "%d,%.9f,%.0f,%.9f,%.9f,%.9f,%.9f,%.9f\n", ...
        i, ...
        timeLog(i), ...
        offsetLog(i), ...
        powerMeanDb(i), ...
        baselinePowerDeltaDb(i), ...
        temporalPowerDeltaDb(i), ...
        temporalComplexDelta(i), ...
        phaseJitterRad(i));
end

fclose(fid);

%% ------------------------------------------------------------------------
% Summary
% -------------------------------------------------------------------------

measureIdx = baselineN + 1:validCount;

summary = "";
summary = summary + sprintf("Label: %s\n", cfg.Label);
summary = summary + sprintf("Long capture seconds: %.3f\n", cfg.LongCaptureSeconds);
summary = summary + sprintf("Baseline seconds: %.3f\n", cfg.BaselineSeconds);
summary = summary + sprintf("Sample rate: %.3f Msps\n", cfg.SampleRate / 1e6);
summary = summary + sprintf("Center frequency: %.3f MHz\n", cfg.CenterFrequency / 1e6);
summary = summary + sprintf("Frequency offset: %.3f Hz\n", freqOffsetHz);
summary = summary + sprintf("NID2: %d\n", NID2);
summary = summary + sprintf("Initial timing offset: %d samples\n", initialTimingOffset);
summary = summary + sprintf("SSB period samples: %d\n", ssbPeriodSamples);
summary = summary + sprintf("Valid extracted SSBs: %d / %d\n", validCount, maxN);
summary = summary + sprintf("Baseline N: %d\n", baselineN);
summary = summary + sprintf("Slow-time Fs: %.3f Hz\n", dop.fsSlowHz);
summary = summary + newline;

summary = summary + sprintf("Temporal metrics after baseline:\n");
summary = summary + sprintf("  baseline_power_delta_db median: %.6f\n", median(baselinePowerDeltaDb(measureIdx), "omitnan"));
summary = summary + sprintf("  baseline_power_delta_db p90:    %.6f\n", prctile(baselinePowerDeltaDb(measureIdx), 90));
summary = summary + sprintf("  temporal_power_delta_db median: %.6f\n", median(temporalPowerDeltaDb(measureIdx), "omitnan"));
summary = summary + sprintf("  temporal_power_delta_db p90:    %.6f\n", prctile(temporalPowerDeltaDb(measureIdx), 90));
summary = summary + sprintf("  temporal_complex_delta median:  %.6f\n", median(temporalComplexDelta(measureIdx), "omitnan"));
summary = summary + sprintf("  phase_jitter_rad median:        %.6f\n", median(phaseJitterRad(measureIdx), "omitnan"));
summary = summary + newline;

summary = summary + sprintf("Doppler-like full-sequence metrics:\n");
summary = summary + sprintf("  dynamic_energy_norm:      %.9f\n", dop.dynamicEnergyNorm);
summary = summary + sprintf("  motion_band_ratio:        %.9f\n", dop.motionBandRatio);
summary = summary + sprintf("  low_band_ratio_0p5_3Hz:   %.9f\n", dop.lowBandRatio);
summary = summary + sprintf("  mid_band_ratio_3_8Hz:     %.9f\n", dop.midBandRatio);
summary = summary + sprintf("  high_band_ratio_8_20Hz:   %.9f\n", dop.highBandRatio);
summary = summary + sprintf("  abs_peak_hz:              %.6f\n", abs(dop.peakHz));
summary = summary + sprintf("  signed_peak_hz:           %.6f\n", dop.peakHz);
summary = summary + sprintf("  centroid_abs_hz:          %.6f\n", dop.centroidAbsHz);

fid = fopen(summaryPath, "w");
fprintf(fid, "%s", summary);
fclose(fid);

fprintf("\n=== Summary ===\n");
fprintf("%s\n", summary);

%% ------------------------------------------------------------------------
% Figure
% -------------------------------------------------------------------------

if cfg.ShowFigure
    fig = figure("Name", "Long-capture SSB motion / Doppler-like analysis", "Color", "w");
else
    fig = figure("Name", "Long-capture SSB motion / Doppler-like analysis", "Color", "w", "Visible", "off");
end

tl = tiledlayout(fig, 2, 2, "TileSpacing", "compact", "Padding", "compact");

ax1 = nexttile(tl, 1);
hold(ax1, "on");
grid(ax1, "on");
plot(ax1, timeLog, temporalPowerDeltaDb, "LineWidth", 1.3, "DisplayName", "Temporal power Δ [dB]");
plot(ax1, timeLog, baselinePowerDeltaDb, "LineWidth", 1.3, "DisplayName", "Baseline power Δ [dB]");
xline(ax1, timeLog(baselineN), "--", "Baseline end");
title(ax1, sprintf("Temporal metrics | %s", cfg.Label));
xlabel(ax1, "Time [s]");
ylabel(ax1, "Score");
legend(ax1, "Location", "best");

ax2 = nexttile(tl, 2);
if validCount > 1
    deltaPower = abs(diff(double(powerDbLog), 1, 2));
    imagesc(ax2, timeLog(2:end), 1:240, deltaPower);
else
    imagesc(ax2, nan(240, 2));
end
axis(ax2, "xy");
colorbar(ax2);
title(ax2, "Δ power by subcarrier over time [dB]");
xlabel(ax2, "Time [s]");
ylabel(ax2, "SSB subcarrier");

ax3 = nexttile(tl, 3);
hold(ax3, "on");
grid(ax3, "on");
plot(ax3, dop.freqHz, dop.spectrumNorm, "LineWidth", 1.3);
xline(ax3, 0, "--");
xline(ax3, -cfg.DcRejectHz, ":");
xline(ax3, cfg.DcRejectHz, ":");
title(ax3, sprintf("Doppler-like spectrum | peak=%.2f Hz | centroid=%.2f Hz", dop.peakHz, dop.centroidAbsHz));
xlabel(ax3, "Frequency [Hz]");
ylabel(ax3, "Normalized power");
xlim(ax3, [-min(25, dop.fsSlowHz/2), min(25, dop.fsSlowHz/2)]);

ax4 = nexttile(tl, 4);
hold(ax4, "on");
grid(ax4, "on");
plot(ax4, 1:240, double(powerDbLog(:, end)), "LineWidth", 1.2, "DisplayName", "last");
plot(ax4, 1:240, baselinePowerDb, "--", "LineWidth", 1.2, "DisplayName", "baseline");
title(ax4, "Power per subcarrier");
xlabel(ax4, "SSB subcarrier");
ylabel(ax4, "Power [dB]");
legend(ax4, "Location", "best");

saveas(fig, figPath);

%% ------------------------------------------------------------------------
% Save MAT
% -------------------------------------------------------------------------

if cfg.SaveRawWaveform
    save(matPath, ...
        "cfg", "freqOffsetHz", "NID2", "initialTimingOffset", ...
        "rxGridLog", "complexVecLog", "complexAlignedLog", "powerDbLog", ...
        "timeLog", "offsetLog", ...
        "baselinePowerDeltaDb", "temporalPowerDeltaDb", "temporalComplexDelta", "phaseJitterRad", ...
        "baselinePowerDb", "baselineComplex", "dop", "summary", "waveform", ...
        "-v7.3");
else
    save(matPath, ...
        "cfg", "freqOffsetHz", "NID2", "initialTimingOffset", ...
        "rxGridLog", "complexVecLog", "complexAlignedLog", "powerDbLog", ...
        "timeLog", "offsetLog", ...
        "baselinePowerDeltaDb", "temporalPowerDeltaDb", "temporalComplexDelta", "phaseJitterRad", ...
        "baselinePowerDb", "baselineComplex", "dop", "summary", ...
        "-v7.3");
end

fprintf("\nSaved outputs:\n");
fprintf("  CSV:     %s\n", csvPath);
fprintf("  MAT:     %s\n", matPath);
fprintf("  Figure:  %s\n", figPath);
fprintf("  Summary: %s\n", summaryPath);

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


function aligned = alignCommonPhase(x, ref)
    x = double(x);
    ref = double(ref);

    alpha = sum(x .* conj(ref)) / (sum(abs(ref).^2) + eps);
    phi = angle(alpha);

    aligned = x * exp(-1j * phi);
end


function dop = computeDopplerFromSequence(Xaligned, timeLog, baselineN, cfg)
    dop = struct();

    X = double(Xaligned);
    N = size(X, 2);

    dt = median(diff(timeLog));
    fsSlow = 1 / dt;

    dop.fsSlowHz = fsSlow;

    % Remove static component using full-sequence mean.
    Xdyn = X - mean(X, 2);

    ref = mean(X(:, 1:baselineN), 2);
    dop.dynamicEnergyNorm = mean(abs(Xdyn(:)).^2) / (mean(abs(ref).^2) + eps);

    n = 0:N-1;
    win = 0.5 - 0.5 * cos(2 * pi * n / max(N - 1, 1));
    Xw = Xdyn .* win;

    S = fftshift(fft(Xw, [], 2), 2);
    spectrum = mean(abs(S).^2, 1);

    freqHz = ((0:N-1) - floor(N/2)) * fsSlow / N;

    totalEnergy = sum(spectrum) + eps;

    motionBand = abs(freqHz) > cfg.DcRejectHz & abs(freqHz) <= min(cfg.HighBandMaxHz, fsSlow / 2);
    lowBand = abs(freqHz) > cfg.DcRejectHz & abs(freqHz) <= cfg.LowBandMaxHz;
    midBand = abs(freqHz) > cfg.LowBandMaxHz & abs(freqHz) <= cfg.MidBandMaxHz;
    highBand = abs(freqHz) > cfg.MidBandMaxHz & abs(freqHz) <= min(cfg.HighBandMaxHz, fsSlow / 2);

    dop.freqHz = freqHz;
    dop.spectrum = spectrum;
    dop.spectrumNorm = spectrum / max(spectrum + eps);

    dop.motionBandRatio = sum(spectrum(motionBand)) / totalEnergy;
    dop.lowBandRatio = sum(spectrum(lowBand)) / totalEnergy;
    dop.midBandRatio = sum(spectrum(midBand)) / totalEnergy;
    dop.highBandRatio = sum(spectrum(highBand)) / totalEnergy;

    if any(motionBand)
        bandSpectrum = spectrum(motionBand);
        bandFreq = freqHz(motionBand);

        [~, maxIdx] = max(bandSpectrum);
        dop.peakHz = bandFreq(maxIdx);

        dop.centroidAbsHz = sum(abs(bandFreq) .* bandSpectrum) / (sum(bandSpectrum) + eps);
    else
        dop.peakHz = NaN;
        dop.centroidAbsHz = NaN;
    end
end


function safeCleanup(rx)
    try
        release(rx);
    catch
    end
end
