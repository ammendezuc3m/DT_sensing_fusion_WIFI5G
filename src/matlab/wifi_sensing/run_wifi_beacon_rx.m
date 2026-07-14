function summary = run_wifi_beacon_rx(varargin)
%RUN_WIFI_BEACON_RX Capture and decode ALBSENS WiFi beacons.

p = inputParser;
addParameter(p, "SerialNumber", "34B73C3");
addParameter(p, "CenterFrequency", 2.412e9);
addParameter(p, "SampleRate", 20e6);
addParameter(p, "Gain", 30);
addParameter(p, "ChannelMapping", 1);
addParameter(p, "CaptureDuration", seconds(1));
addParameter(p, "OutputDir", fullfile("results", "wifi_matlab_rx"));
addParameter(p, "ExpectedSSID", "SENSING_WIFI");
addParameter(p, "ExpectedBSSID", "021122334455");
addParameter(p, "VerboseRejects", true);
parse(p, varargin{:});
cfg = p.Results;

if ~isfolder(cfg.OutputDir), mkdir(cfg.OutputDir); end
captureFile = fullfile(cfg.OutputDir, "raw_wifi_capture.mat");

capture_wifi_usrp(SerialNumber=cfg.SerialNumber, CenterFrequency=cfg.CenterFrequency, ...
    SampleRate=cfg.SampleRate, Gain=cfg.Gain, ChannelMapping=cfg.ChannelMapping, ...
    CaptureDuration=cfg.CaptureDuration, OutputFile=captureFile);

summary = decode_wifi_beacons_offline(InputFile=captureFile, OutputDir=cfg.OutputDir, ...
    ExpectedSSID=cfg.ExpectedSSID, ExpectedBSSID=cfg.ExpectedBSSID, ...
    VerboseRejects=cfg.VerboseRejects);
end
