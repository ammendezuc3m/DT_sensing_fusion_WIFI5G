function summary = decode_wifi_beacons_offline(varargin)
%DECODE_WIFI_BEACONS_OFFLINE Decode OFDM beacons and retain ALBSENS CSI.

p = inputParser;
addParameter(p, "InputFile", fullfile("results", "wifi_matlab_rx", "raw_wifi_capture.mat"));
addParameter(p, "OutputDir", fullfile("results", "wifi_matlab_rx"));
addParameter(p, "ExpectedSSID", "SENSING_WIFI");
addParameter(p, "ExpectedBSSID", "021122334455");
addParameter(p, "VerboseRejects", true);
parse(p, varargin{:});
cfg = p.Results;

inputFile = string(cfg.InputFile);
outputDir = string(cfg.OutputDir);
if ~isfolder(outputDir), mkdir(outputDir); end

S = load(inputFile);
if ~isfield(S, "capturedData"), error("Input file does not contain capturedData."); end
capturedData = S.capturedData;
if isfield(S, "sampleRate"), sampleRate = double(S.sampleRate); else, sampleRate = 20e6; end

expectedSSID = string(cfg.ExpectedSSID);
expectedBSSID = upper(erase(string(cfg.ExpectedBSSID), [":","-"]));

acceptedCSI = complex(zeros(52,0,"single"));
packetCounter = zeros(0,1,"uint32");
packetOffset = zeros(0,1,"uint64");
sequenceNumber = zeros(0,1,"uint16");
snrDB = zeros(0,1);
cfoHz = zeros(0,1);
ssid = strings(0,1);
bssid = strings(0,1);
psduLength = zeros(0,1);
mcs = zeros(0,1);

searchOffset = 0;
decodedPackets = 0;
decodedBeacons = 0;
acceptedCount = 0;
rejectedCount = 0;

fprintf("Decoding %.6f s of IQ (%d samples)...\n", size(capturedData,1)/sampleRate, size(capturedData,1));

while searchOffset < size(capturedData,1)
    previousOffset = searchOffset;
    try
        [bitsData, decParams, searchOffset, res] = recoverOFDMBits(capturedData, searchOffset);
    catch ME
        warning("recoverOFDMBits failed at offset %d: %s", previousOffset, ME.message);
        searchOffset = previousOffset + 80;
        continue;
    end
    if searchOffset <= previousOffset, searchOffset = previousOffset + 80; end
    if isempty(bitsData), continue; end

    decodedPackets = decodedPackets + 1;
    [cfgMAC, ~, decodeStatus] = wlanMPDUDecode(bitsData, SuppressWarnings=true);
    if decodeStatus
        rejectedCount = rejectedCount + 1;
        if cfg.VerboseRejects, fprintf("REJECT offset=%d: MPDU decode/FCS failed\n", res.PacketOffset); end
        continue;
    end
    if ~matches(string(cfgMAC.FrameType), "Beacon"), continue; end

    decodedBeacons = decodedBeacons + 1;
    currentSSID = string(cfgMAC.ManagementConfig.SSID);
    currentBSSID = upper(erase(string(cfgMAC.Address3), [":","-"]));
    vendor = parse_albsens_vendor_ie(cfgMAC.ManagementConfig.InformationElements);

    identityOK = currentSSID == expectedSSID && currentBSSID == expectedBSSID && vendor.Valid;
    if ~identityOK
        rejectedCount = rejectedCount + 1;
        if cfg.VerboseRejects
            fprintf("FOREIGN beacon offset=%d SSID=%s BSSID=%s vendor=%s\n", ...
                res.PacketOffset, currentSSID, currentBSSID, vendor.Reason);
        end
        continue;
    end

    if ~isfield(res, "ChanEstNonHT") || isempty(res.ChanEstNonHT)
        rejectedCount = rejectedCount + 1;
        fprintf("REJECT offset=%d: ChanEstNonHT missing\n", res.PacketOffset);
        continue;
    end

    thisCSI = squeeze(res.ChanEstNonHT);
    thisCSI = thisCSI(:);
    if numel(thisCSI) ~= 52
        rejectedCount = rejectedCount + 1;
        fprintf("REJECT offset=%d: CSI has %d values, expected 52\n", res.PacketOffset, numel(thisCSI));
        continue;
    end

    acceptedCount = acceptedCount + 1;
    acceptedCSI(:,acceptedCount) = single(thisCSI);
    packetCounter(acceptedCount,1) = uint32(vendor.PacketCounter);
    packetOffset(acceptedCount,1) = uint64(res.PacketOffset);
    sequenceNumber(acceptedCount,1) = uint16(0);
    if isfield(res, "LLTFSNR"), snrDB(acceptedCount,1) = double(res.LLTFSNR); else, snrDB(acceptedCount,1) = NaN; end
    if isfield(res, "CFOEstimate"), cfoHz(acceptedCount,1) = double(res.CFOEstimate); else, cfoHz(acceptedCount,1) = NaN; end
    ssid(acceptedCount,1) = currentSSID;
    bssid(acceptedCount,1) = currentBSSID;
    psduLength(acceptedCount,1) = double(decParams.PSDULength);
    mcs(acceptedCount,1) = double(decParams.MCS);

    fprintf("ACCEPT offset=%d counter=%u SSID=%s BSSID=%s SNR=%.2f dB CFO=%.1f Hz\n", ...
        res.PacketOffset, packetCounter(acceptedCount), currentSSID, currentBSSID, ...
        snrDB(acceptedCount), cfoHz(acceptedCount));
end

csi = acceptedCSI.';
metadata = table(packetCounter, packetOffset, sequenceNumber, snrDB, cfoHz, ...
    ssid, bssid, psduLength, mcs);

matFile = fullfile(outputDir, "accepted_wifi_csi.mat");
csvFile = fullfile(outputDir, "accepted_wifi_packets.csv");
jsonFile = fullfile(outputDir, "summary.json");

save(matFile, "csi", "packetCounter", "packetOffset", "sequenceNumber", ...
    "snrDB", "cfoHz", "ssid", "bssid", "psduLength", "mcs", "sampleRate", "-v7.3");
writetable(metadata, csvFile);

summary = struct("InputFile", inputFile, "SampleRateHz", sampleRate, ...
    "Samples", size(capturedData,1), "DurationSec", size(capturedData,1)/sampleRate, ...
    "DecodedPackets", decodedPackets, "DecodedBeacons", decodedBeacons, ...
    "AcceptedBeacons", acceptedCount, "RejectedPackets", rejectedCount, ...
    "CSIShape", [size(csi,1), size(csi,2)], "OutputMAT", string(matFile), ...
    "OutputCSV", string(csvFile));

fid = fopen(jsonFile, "w");
if fid < 0, error("Could not open JSON output file."); end
cleanupObj = onCleanup(@()fclose(fid)); %#ok<NASGU>
fprintf(fid, "%s", jsonencode(summary, PrettyPrint=true));

fprintf("\nMATLAB WLAN RX summary\n");
fprintf("  decoded packets: %d\n", decodedPackets);
fprintf("  decoded beacons: %d\n", decodedBeacons);
fprintf("  accepted ALBSENS beacons: %d\n", acceptedCount);
fprintf("  rejected: %d\n", rejectedCount);
fprintf("  CSI shape: %d x %d\n", size(csi,1), size(csi,2));
fprintf("  MAT: %s\n", matFile);
fprintf("  CSV: %s\n", csvFile);
fprintf("  JSON: %s\n", jsonFile);
end
