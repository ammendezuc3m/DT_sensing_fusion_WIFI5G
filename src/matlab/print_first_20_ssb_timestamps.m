%% Print first 20 SSB timestamps from latest timestamped dataSSB capture

clear;
clc;

scriptDir = fileparts(mfilename("fullpath"));
projectRoot = fileparts(fileparts(scriptDir));

dataRoot = fullfile(projectRoot, "data", "dataset_datassb");

files = dir(fullfile(dataRoot, "**", "*timestamps*.mat"));

if isempty(files)
    error("No timestamped MAT files found under: %s", dataRoot);
end

[~, newestIdx] = max([files.datenum]);
matFile = fullfile(files(newestIdx).folder, files(newestIdx).name);

fprintf("Loading latest timestamped file:\n%s\n\n", matFile);

load(matFile, ...
    "validMask", ...
    "cfg", ...
    "timingOffsets", ...
    "captureStartUnixTime", ...
    "captureEndUnixTime", ...
    "ssbTimingReferenceUnixTime", ...
    "ssbStartUnixTimeApprox", ...
    "ssbInterArrivalSeconds", ...
    "ssbTimeOffsetFromCaptureStartSeconds", ...
    "ssbTimingReferenceRelativeSeconds", ...
    "ssbStartRelativeSecondsApprox", ...
    "labelName", ...
    "orientationName", ...
    "datasetName", ...
    "blockIndex", ...
    "sessionId");

validIdx = find(validMask);
nPrint = min(20, numel(validIdx));
idx = validIdx(1:nPrint);

fprintf("Dataset:     %s\n", string(datasetName));
fprintf("Label:       %s\n", string(labelName));
fprintf("Orientation: %s\n", string(orientationName));
fprintf("Block:       %d\n", blockIndex);
fprintf("Session ID:  %s\n", string(sessionId));
fprintf("Valid SSBs:  %d\n\n", numel(validIdx));

ssbTimeMadrid = datetime(ssbTimingReferenceUnixTime(idx), ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "Europe/Madrid", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

ssbStartApproxMadrid = datetime(ssbStartUnixTimeApprox(idx), ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "Europe/Madrid", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

captureStartMadrid = datetime(captureStartUnixTime(idx), ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "Europe/Madrid", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

captureEndMadrid = datetime(captureEndUnixTime(idx), ...
    "ConvertFrom", "posixtime", ...
    "TimeZone", "Europe/Madrid", ...
    "Format", "yyyy-MM-dd HH:mm:ss.SSSSSS");

T = table();

T.capture_index = idx(:);
T.ssb_time_madrid = string(ssbTimeMadrid(:));
T.ssb_unix_time = ssbTimingReferenceUnixTime(idx);

T.ssb_start_approx_madrid = string(ssbStartApproxMadrid(:));
T.ssb_start_approx_unix_time = ssbStartUnixTimeApprox(idx);

T.relative_time_from_block_start_s = ssbTimingReferenceRelativeSeconds(idx);
T.relative_start_approx_from_block_start_s = ssbStartRelativeSecondsApprox(idx);

T.inter_arrival_s = ssbInterArrivalSeconds(idx);

T.timing_offset_samples = timingOffsets(idx);
T.ssb_offset_from_capture_start_s = ssbTimeOffsetFromCaptureStartSeconds(idx);

T.capture_start_madrid = string(captureStartMadrid(:));
T.capture_end_madrid = string(captureEndMadrid(:));

fprintf("=== First %d SSB timestamps ===\n\n", nPrint);

disp(T);

fprintf("\nMeaning of main columns:\n");
fprintf("ssb_time_madrid                  = estimated SSB timing reference time\n");
fprintf("ssb_start_approx_madrid          = estimated start of the SSB window in dataSSB\n");
fprintf("inter_arrival_s                  = time difference from previous valid SSB\n");
fprintf("timing_offset_samples            = SSB position inside the 20 ms IQ buffer\n");
fprintf("ssb_offset_from_capture_start_s  = timing_offset_samples / sampleRate\n");

csvFile = replace(matFile, ".mat", "_first20_ssb_timestamps.csv");
writetable(T, csvFile);

fprintf("\nSaved CSV:\n%s\n", csvFile);
