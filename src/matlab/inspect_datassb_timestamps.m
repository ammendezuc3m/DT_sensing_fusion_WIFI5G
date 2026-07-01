%% Inspect dataSSB SSB timestamps
% This script loads the latest timestamped dataSSB dataset block and exports
% one CSV row per captured SSB.
%
% It prints the first SSB timestamps and saves a CSV file with all of them.

clear;
clc;

%% ------------------------------------------------------------------------
% Locate project and latest timestamped MAT file
% -------------------------------------------------------------------------

scriptDir = fileparts(mfilename("fullpath"));
projectRoot = fileparts(fileparts(scriptDir));

dataRoot = fullfile(projectRoot, "data", "dataset_datassb");

files = dir(fullfile(dataRoot, "**", "*timestamps*.mat"));

if isempty(files)
    % Fallback: some files may not include "timestamps" in their name.
    files = dir(fullfile(dataRoot, "**", "*.mat"));
end

if isempty(files)
    error("No dataset MAT files found under: %s", dataRoot);
end

[~, newestIdx] = max([files.datenum]);
matFile = fullfile(files(newestIdx).folder, files(newestIdx).name);

fprintf("Loading latest dataset file:\n%s\n", matFile);

load(matFile, ...
    "validMask", ...
    "cfg", ...
    "summary", ...
    "timingOffsets", ...
    "captureStartUnixTime", ...
    "captureEndUnixTime", ...
    "captureMidUnixTime", ...
    "ssbTimingReferenceUnixTime", ...
    "ssbStartUnixTimeApprox", ...
    "ssbTimingReferenceRelativeSeconds", ...
    "ssbStartRelativeSecondsApprox", ...
    "ssbTimeOffsetFromCaptureStartSeconds", ...
    "ssbInterArrivalSeconds", ...
    "blockStartUnixTime", ...
    "blockStartIsoUTC", ...
    "labelName", ...
    "orientationName", ...
    "datasetName", ...
    "blockIndex", ...
    "sessionId");

%% ------------------------------------------------------------------------
% Build timestamp table
% -------------------------------------------------------------------------

validIdx = find(validMask);
nValid = numel(validIdx);

fprintf("\n=== Dataset info ===\n");
fprintf("Dataset:        %s\n", string(datasetName));
fprintf("Label:          %s\n", string(labelName));
fprintf("Orientation:    %s\n", string(orientationName));
fprintf("Block index:    %d\n", blockIndex);
fprintf("Session ID:     %s\n", string(sessionId));
fprintf("Valid SSBs:     %d\n", nValid);
fprintf("Block start UTC:%s\n", string(blockStartIsoUTC));

idx = validIdx(:);

ssbUnix = ssbTimingReferenceUnixTime(idx);
ssbStartApproxUnix = ssbStartUnixTimeApprox(idx);

captureStartUnix = captureStartUnixTime(idx);
captureEndUnix = captureEndUnixTime(idx);
captureMidUnix = captureMidUnixTime(idx);

timingOffsetSamples = timingOffsets(idx);
ssbOffsetFromCaptureStart = ssbTimeOffsetFromCaptureStartSeconds(idx);
interArrival = ssbInterArrivalSeconds(idx);

relativeFromBlockStart = ssbTimingReferenceRelativeSeconds(idx);
relativeStartApprox = ssbStartRelativeSecondsApprox(idx);

% UTC datetime
ssbDateTimeUTC = datetime(ssbUnix, ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "UTC", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

ssbStartApproxDateTimeUTC = datetime(ssbStartApproxUnix, ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "UTC", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

captureStartDateTimeUTC = datetime(captureStartUnix, ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "UTC", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

captureEndDateTimeUTC = datetime(captureEndUnix, ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "UTC", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

% Madrid/local view
ssbDateTimeMadrid = ssbDateTimeUTC;
ssbDateTimeMadrid.TimeZone = "Europe/Madrid";
ssbDateTimeMadrid.Format = "yyyy-MM-dd HH:mm:ss.SSSSSS";

ssbStartApproxDateTimeMadrid = ssbStartApproxDateTimeUTC;
ssbStartApproxDateTimeMadrid.TimeZone = "Europe/Madrid";
ssbStartApproxDateTimeMadrid.Format = "yyyy-MM-dd HH:mm:ss.SSSSSS";

T = table();

T.capture_index = idx;
T.ssb_time_utc = string(ssbDateTimeUTC);
T.ssb_time_madrid = string(ssbDateTimeMadrid);
T.ssb_unix_time = ssbUnix;

T.ssb_start_approx_utc = string(ssbStartApproxDateTimeUTC);
T.ssb_start_approx_madrid = string(ssbStartApproxDateTimeMadrid);
T.ssb_start_approx_unix_time = ssbStartApproxUnix;

T.relative_time_from_block_start_s = relativeFromBlockStart;
T.relative_start_approx_from_block_start_s = relativeStartApprox;

T.inter_arrival_s = interArrival;
T.timing_offset_samples = timingOffsetSamples;
T.ssb_offset_from_capture_start_s = ssbOffsetFromCaptureStart;

T.capture_start_utc = string(captureStartDateTimeUTC);
T.capture_end_utc = string(captureEndDateTimeUTC);
T.capture_start_unix_time = captureStartUnix;
T.capture_end_unix_time = captureEndUnix;
T.capture_mid_unix_time = captureMidUnix;

%% ------------------------------------------------------------------------
% Print first rows
% -------------------------------------------------------------------------

nPrint = min(30, height(T));

fprintf("\n=== First %d SSB timestamps ===\n", nPrint);
disp(T(1:nPrint, { ...
    "capture_index", ...
    "ssb_time_madrid", ...
    "relative_time_from_block_start_s", ...
    "inter_arrival_s", ...
    "timing_offset_samples"}));

fprintf("\n=== Inter-arrival summary ===\n");
validInter = T.inter_arrival_s(~isnan(T.inter_arrival_s));

fprintf("N inter-arrivals: %d\n", numel(validInter));
fprintf("Mean:   %.6f s\n", mean(validInter, "omitnan"));
fprintf("Median: %.6f s\n", median(validInter, "omitnan"));
fprintf("P05:    %.6f s\n", prctile(validInter, 5));
fprintf("P95:    %.6f s\n", prctile(validInter, 95));
fprintf("Min:    %.6f s\n", min(validInter));
fprintf("Max:    %.6f s\n", max(validInter));

%% ------------------------------------------------------------------------
% Save CSV
% -------------------------------------------------------------------------

csvFile = replace(matFile, ".mat", "_ssb_timestamps.csv");

writetable(T, csvFile);

fprintf("\nSaved timestamp CSV:\n%s\n", csvFile);

%% ------------------------------------------------------------------------
% Optional plot
% -------------------------------------------------------------------------

figure("Name", "SSB inter-arrival times");
plot(T.capture_index, T.inter_arrival_s, ".");
grid on;
xlabel("Capture index");
ylabel("SSB inter-arrival time [s]");
title("Estimated SSB inter-arrival time");

figure("Name", "SSB relative times");
plot(T.capture_index, T.relative_time_from_block_start_s, ".");
grid on;
xlabel("Capture index");
ylabel("SSB time from block start [s]");
title("Estimated SSB timestamp relative to block start");
