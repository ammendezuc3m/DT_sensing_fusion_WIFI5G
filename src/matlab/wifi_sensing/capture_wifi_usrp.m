function captureFile = capture_wifi_usrp(varargin)
%CAPTURE_WIFI_USRP Capture raw 20 MHz WiFi IQ from a USRP B210.

p = inputParser;
addParameter(p, "SerialNumber", "34B73C3");
addParameter(p, "CenterFrequency", 2.412e9);
addParameter(p, "SampleRate", 20e6);
addParameter(p, "Gain", 30);
addParameter(p, "ChannelMapping", 1);
addParameter(p, "CaptureDuration", seconds(1));
addParameter(p, "OutputFile", fullfile("results", "wifi_matlab_rx", "raw_wifi_capture.mat"));
parse(p, varargin{:});
cfg = p.Results;

outputFile = string(cfg.OutputFile);
outputDir = fileparts(outputFile);
if strlength(outputDir) > 0 && ~isfolder(outputDir)
    mkdir(outputDir);
end

fprintf("Creating B210 receiver...\n");
rx = hSDRReceiver("B210", ...
    CenterFrequency=cfg.CenterFrequency, ...
    SampleRate=cfg.SampleRate, ...
    DeviceAddress=cfg.SerialNumber, ...
    ChannelMapping=cfg.ChannelMapping);
rx.Gain = cfg.Gain;
rx.OutputDataType = "single";
cleanupObj = onCleanup(@()releaseSafely(rx)); %#ok<NASGU>

fprintf("Capturing %.3f s at %.3f MHz...\n", seconds(cfg.CaptureDuration), cfg.CenterFrequency/1e6);
[capturedData, captureTimestamp] = capture(rx, cfg.CaptureDuration);

sampleRate = double(rx.SampleRate);
centerFrequency = double(rx.CenterFrequency);
gainDB = double(cfg.Gain);
serialNumber = string(cfg.SerialNumber);
channelMapping = double(cfg.ChannelMapping);
captureDurationSec = seconds(cfg.CaptureDuration);

fprintf("Captured %d samples (%.6f s).\n", size(capturedData,1), size(capturedData,1)/sampleRate);
save(outputFile, "capturedData", "captureTimestamp", "sampleRate", "centerFrequency", ...
    "gainDB", "serialNumber", "channelMapping", "captureDurationSec", "-v7.3");

captureFile = char(outputFile);
fprintf("Saved: %s\n", captureFile);
end

function releaseSafely(rx)
try
    release(rx);
catch
end
end
