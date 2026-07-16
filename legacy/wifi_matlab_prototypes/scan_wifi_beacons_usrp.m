function detectedBeaconsInfo = scan_wifi_beacons_usrp(varargin)
%SCAN_WIFI_BEACONS_USRP Scan Wi-Fi beacon channels with a USRP B210.
%
% This function uses the WLAN Toolbox receiver helpers already present in:
%   src/matlab/wifi_sensing
%
% It captures IQ with the B210, detects and demodulates legacy WLAN packets,
% keeps valid Beacon frames, and displays:
%   SSID, BSSID, SNR, primary channel, operating channel, channel width,
%   band and inferred Wi-Fi generation.
%
% Example:
%   scan_wifi_beacons_usrp( ...
%       "Mode","OFDM", ...
%       "Channels",1:13, ...
%       "Gain",35, ...
%       "CaptureMs",500, ...
%       "ChannelMapping",1);
%
% Quick test on channel 13:
%   scan_wifi_beacons_usrp("Mode","OFDM","Channels",13,"CaptureMs",1000);
%
% Notes:
% - Connect the antenna to RX2.
% - ChannelMapping=1 selects the first logical RX chain.
% - OFDM mode uses 20 Msps.
% - DSSS mode uses 11 Msps and is useful for older 2.4 GHz beacons.
% - To approximate a complete 2.4 GHz scan, run both OFDM and DSSS modes.

p = inputParser;
addParameter(p,"Mode","OFDM");
addParameter(p,"Channels",1:13);
addParameter(p,"Gain",35);
addParameter(p,"CaptureMs",500);
addParameter(p,"ChannelMapping",1);
addParameter(p,"Plot",true);
addParameter(p,"VerbosePackets",false);
parse(p,varargin{:});
opt = p.Results;

mode = upper(string(opt.Mode));
if ~ismember(mode,["OFDM","DSSS"])
    error("Mode must be OFDM or DSSS.");
end

channels = double(opt.Channels(:).');
if any(channels < 1 | channels > 14 | mod(channels,1) ~= 0)
    error("Channels must contain integer Wi-Fi channels between 1 and 14.");
end

repoRoot = pwd;
helperPath = fullfile(repoRoot,"src","matlab","wifi_sensing");
if ~isfolder(helperPath)
    error("Helper directory not found: %s",helperPath);
end
addpath(helperPath,"-begin");

requiredHelpers = ["hSDRReceiver","recoverOFDMBits","recoverDSSSBits"];
for name = requiredHelpers
    if exist(name,"file") == 0
        error("Required helper not found on MATLAB path: %s",name);
    end
end

if mode == "OFDM"
    config = "OFDM, band 2.4";
    sampleRate = 20e6;
    channels = channels(channels <= 13);
else
    config = "DSSS, band 2.4";
    sampleRate = 11e6;
end

if isempty(channels)
    error("No valid channels remain for the selected mode.");
end

centerFrequencies = wlanChannelFrequency(channels,2.4);
captureTime = milliseconds(opt.CaptureMs);

fprintf("USRP Wi-Fi beacon scanner\n");
fprintf("  Mode             : %s\n",config);
fprintf("  Sample rate      : %.3f Msps\n",sampleRate/1e6);
fprintf("  Gain             : %.1f dB\n",opt.Gain);
fprintf("  Channel mapping  : %d\n",opt.ChannelMapping);
fprintf("  Capture/channel  : %.1f ms\n",opt.CaptureMs);
fprintf("  Channels         : %s\n\n",mat2str(channels));

deviceOptions = string(hSDRBase.ListOfUSRPs);
fprintf("USRP device options: %s\n",strjoin(deviceOptions,", "));

% For a B210, hSDRReceiver normally accepts "B210".
if any(strcmpi(deviceOptions,"B210"))
    deviceName = "B210";
elseif ~isempty(deviceOptions)
    deviceName = deviceOptions(1);
else
    error("No supported USRP device name was reported by hSDRBase.");
end

sdrReceiver = hSDRReceiver(deviceName);
cleanupObj = onCleanup(@() release(sdrReceiver)); %#ok<NASGU>

sdrReceiver.SampleRate = sampleRate;
sdrReceiver.Gain = opt.Gain;
sdrReceiver.ChannelMapping = opt.ChannelMapping;
sdrReceiver.OutputDataType = "single";

actualRate = sdrReceiver.SampleRate;
osf = actualRate/sampleRate;

fprintf("Selected device   : %s\n",deviceName);
fprintf("Actual sample rate: %.6f Msps\n\n",actualRate/1e6);

APs = struct( ...
    "SSID",{}, ...
    "BSSID",{}, ...
    "SNR_dB",{}, ...
    "Beacon_Channel",{}, ...
    "Operating_Channel",{}, ...
    "Channel_Width_MHz",{}, ...
    "Band",{}, ...
    "Mode",{}, ...
    "Frequency_MHz",{}, ...
    "Offset",{} );

indexAP = 1;

for i = 1:numel(channels)
    channel = channels(i);
    frequency = centerFrequencies(i);

    fprintf("Scanning channel %d at %.3f MHz...\n", ...
        channel,frequency/1e6);

    sdrReceiver.CenterFrequency = frequency;
    capturedData = capture(sdrReceiver,captureTime);

    if osf ~= 1
        capturedData = resample( ...
            capturedData, ...
            round(sampleRate/1e3), ...
            round(actualRate/1e3));
    end

    searchOffset = 0;
    decodedHere = 0;

    while searchOffset < length(capturedData)
        previousOffset = searchOffset;

        if mode == "DSSS"
            [bitsData,decParams,searchOffset,res] = ...
                recoverDSSSBits(capturedData,searchOffset);
        else
            [bitsData,decParams,searchOffset,res] = ...
                recoverOFDMBits(capturedData,searchOffset);
        end

        % Guard against a helper returning the same offset indefinitely.
        if searchOffset <= previousOffset
            searchOffset = previousOffset + 1;
        end

        if isempty(bitsData)
            continue;
        end

        [cfgMAC,~,decodeStatus] = wlanMPDUDecode( ...
            bitsData,SuppressWarnings=true);

        if decodeStatus
            continue;
        end

        if opt.VerbosePackets
            payloadSize = floor(length(bitsData)/8);
            fprintf("  Packet: %s/%s, %d bytes", ...
                cfgMAC.getType,cfgMAC.getSubtype,payloadSize);
            if mode == "OFDM"
                fprintf(", %s, code rate %s\n", ...
                    string(decParams.modulation), ...
                    string(decParams.coderate));
            else
                fprintf(", %s, data rate %s\n", ...
                    string(decParams.modulation), ...
                    string(decParams.dataRate));
            end
        end

        if ~matches(cfgMAC.FrameType,"Beacon")
            continue;
        end

        if isempty(cfgMAC.ManagementConfig.SSID)
            ssid = "Hidden";
        else
            ssid = string(cfgMAC.ManagementConfig.SSID);
        end

        bssid = string(cfgMAC.Address3);
        [wifiMode,widthMHz,operatingChannel,primaryChannel] = ...
            determineModeLocal( ...
                cfgMAC.ManagementConfig.InformationElements);

        if isempty(primaryChannel) || isnan(primaryChannel)
            primaryChannel = channel;
        end
        if isempty(operatingChannel) || isnan(operatingChannel)
            operatingChannel = channel;
        end

        % Ignore a beacon decoded while scanning a non-primary channel.
        if channel ~= primaryChannel
            continue;
        end

        if mode == "OFDM" && isfield(res,"LLTFSNR")
            snrDB = double(res.LLTFSNR);
        else
            snrDB = NaN;
        end

        APs(indexAP).SSID = ssid;
        APs(indexAP).BSSID = bssid;
        APs(indexAP).SNR_dB = snrDB;
        APs(indexAP).Beacon_Channel = primaryChannel;
        APs(indexAP).Operating_Channel = operatingChannel;
        APs(indexAP).Channel_Width_MHz = widthMHz;
        APs(indexAP).Band = 2.4;
        APs(indexAP).Mode = wifiMode;
        APs(indexAP).Frequency_MHz = frequency/1e6;
        APs(indexAP).Offset = double(res.PacketOffset);

        fprintf("  Beacon: %-28s BSSID=%s SNR=%6.2f dB BW=%s MHz Mode=%s\n", ...
            ssid,bssid,snrDB,widthMHz,wifiMode);

        indexAP = indexAP + 1;
        decodedHere = decodedHere + 1;
    end

    fprintf("  Valid beacons decoded on channel %d: %d\n\n", ...
        channel,decodedHere);
end

if isempty(APs)
    detectedBeaconsInfo = table;
    fprintf("No valid Wi-Fi beacons were decoded.\n");
    fprintf("Try increasing CaptureMs, changing Gain, or running DSSS mode.\n");
    return;
end

detectedBeaconsInfo = struct2table(APs,"AsArray",true);

% Deduplicate repeated beacon receptions using BSSID.
[~,uniqueIdx] = unique(detectedBeaconsInfo.BSSID,"stable");
detectedBeaconsInfo = detectedBeaconsInfo(uniqueIdx,:);

detectedBeaconsInfo = sortrows( ...
    detectedBeaconsInfo, ...
    ["Beacon_Channel","SSID"]);

fprintf("\nDetected access points:\n");
disp(detectedBeaconsInfo(:,{ ...
    "SSID","BSSID","SNR_dB","Beacon_Channel", ...
    "Operating_Channel","Channel_Width_MHz", ...
    "Frequency_MHz","Mode"}));

if opt.Plot
    plotBeaconOverlapLocal(detectedBeaconsInfo);
end

outputDir = fullfile(repoRoot,"results","wifi_usrp_scan");
if ~isfolder(outputDir)
    mkdir(outputDir);
end

timestamp = string(datetime("now","Format","yyyyMMdd_HHmmss"));
csvPath = fullfile(outputDir,"beacons_" + timestamp + ".csv");
matPath = fullfile(outputDir,"beacons_" + timestamp + ".mat");

writetable(detectedBeaconsInfo,csvPath);
save(matPath,"detectedBeaconsInfo","opt");

fprintf("Saved CSV: %s\n",csvPath);
fprintf("Saved MAT: %s\n",matPath);

end


function [mode,bw,operatingChannel,primaryChannel] = ...
    determineModeLocal(informationElements)

mode = "802.11 legacy";
bw = "20";
operatingChannel = NaN;
primaryChannel = NaN;

if isempty(informationElements)
    return;
end

elementIDs = cell2mat(informationElements(:,1));
IDs = elementIDs(:,1);

htElement = [];
vhtElement = [];

if any(IDs == 61)
    htElement = informationElements{find(IDs == 61,1),2};
end
if any(IDs == 192)
    vhtElement = informationElements{find(IDs == 192,1),2};
end

if any(elementIDs(IDs == 255,2) == 35)
    mode = "802.11ax";
elseif any(IDs == 191)
    mode = "802.11ac";
elseif any(IDs == 45)
    mode = "802.11n";
elseif any(IDs == 3)
    mode = "802.11b/g";
end

if any(IDs == 3)
    ds = informationElements{find(IDs == 3,1),2};
    if ~isempty(ds)
        primaryChannel = double(ds(1));
        operatingChannel = primaryChannel;
    end
end

if ~isempty(htElement)
    primaryChannel = double(htElement(1));
    [bw,operatingChannel] = determineWidthLocal( ...
        htElement,vhtElement);
end

end


function [bw,operatingChannel] = determineWidthLocal(htElement,vhtElement)

bw = "20";
operatingChannel = double(htElement(1));

if numel(htElement) < 2
    return;
end

htOperationInfoBits = int2bit(htElement(2),5*8,false);

if htOperationInfoBits(3) == 1
    bw = "40";
    secondaryOffset = bit2int(htOperationInfoBits(1:2),2,false);
    if secondaryOffset == 1
        operatingChannel = double(htElement(1)) + 2;
    elseif secondaryOffset == 3
        operatingChannel = double(htElement(1)) - 2;
    end
end

if isempty(vhtElement) || numel(vhtElement) < 3
    return;
end

CW = double(vhtElement(1));
CCFS0 = double(vhtElement(2));
CCFS1 = double(vhtElement(3));

if htOperationInfoBits(3) == 0
    bw = "20";
    operatingChannel = CCFS0;
elseif CW == 0
    bw = "40";
    operatingChannel = CCFS0;
elseif CCFS1 == 0
    bw = "80";
    operatingChannel = CCFS0;
elseif abs(CCFS1-CCFS0) == 8
    bw = "160";
    operatingChannel = CCFS1;
else
    bw = "80+80";
    operatingChannel = CCFS0;
end

end


function plotBeaconOverlapLocal(tbl)

figure("Name","USRP Wi-Fi Beacon Scanner","Color","white");
hold on;
grid on;

for i = 1:height(tbl)
    center = tbl.Frequency_MHz(i);
    snr = tbl.SNR_dB(i);

    if isnan(snr)
        heightValue = 20;
    else
        heightValue = max(5,snr);
    end

    widthText = string(tbl.Channel_Width_MHz(i));
    widthValue = str2double(extractBefore(widthText + "+","+" ));
    if isnan(widthValue)
        widthValue = 20;
    end

    span = max(12,widthValue*0.7);
    x = linspace(center-span,center+span,241);
    sigma = max(4,widthValue/2.8);
    y = heightValue .* exp(-0.5*((x-center)./sigma).^2);

    plot(x,y,"LineWidth",1.5);
    area(x,y,"FaceAlpha",0.08,"EdgeAlpha",0);

    text(center,heightValue+0.8, ...
        sprintf("%s\nCH %d · %.0f MHz · %s MHz", ...
        tbl.SSID(i), ...
        tbl.Beacon_Channel(i), ...
        center, ...
        tbl.Channel_Width_MHz(i)), ...
        "HorizontalAlignment","center", ...
        "VerticalAlignment","bottom", ...
        "FontSize",8);
end

xlabel("Frequency (MHz)");
ylabel("Decoded beacon SNR (dB)");
title("Wi-Fi APs decoded by USRP B210");
xlim([2395 2490]);

hold off;

end
