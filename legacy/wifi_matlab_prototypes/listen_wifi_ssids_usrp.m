function listen_wifi_ssids_usrp()
clc;

serialNumber    = "34B73C3";
centerFrequency = 2.462e9;
sampleRate      = 20e6;
masterClockRate = 20e6;
gainDB          = 65;
samplesPerFrame = 200000;
channelMapping  = 1;

repoRoot = fileparts(fileparts(fileparts(fileparts(mfilename("fullpath")))));
helperDir = fullfile(repoRoot,"src","matlab","wifi_sensing");
addpath(helperDir,"-begin");

if exist("recoverOFDMBits","file") == 0
    error("No encuentro recoverOFDMBits.m en %s",helperDir);
end

rx = comm.SDRuReceiver( ...
    Platform="B210", ...
    SerialNum=char(serialNumber), ...
    CenterFrequency=centerFrequency, ...
    MasterClockRate=masterClockRate, ...
    DecimationFactor=masterClockRate/sampleRate, ...
    Gain=gainDB, ...
    ChannelMapping=channelMapping, ...
    SamplesPerFrame=samplesPerFrame, ...
    OutputDataType="single");

cleanupObj = onCleanup(@() release(rx)); %#ok<NASGU>

fprintf("USRP B210 - receptor de beacons Wi-Fi\n");
fprintf("Serial      : %s\n",serialNumber);
fprintf("Frecuencia  : %.3f MHz (canal 11)\n",centerFrequency/1e6);
fprintf("Tasa        : %.3f Msps\n",sampleRate/1e6);
fprintf("Ganancia RX : %.1f dB\n",gainDB);
fprintf("Antena      : RX2\n");
fprintf("Detener     : Ctrl+C\n\n");

seenBSSIDs = strings(0,1);
buffer = complex(zeros(0,1,"single"));
maxBufferSamples = round(0.050*sampleRate);
frameCount = 0;
overflowCount = 0;
decodedCount = 0;
lastStatus = tic;

while true
    [iq,validLength,overflow] = rx();

    if overflow
        overflowCount = overflowCount + 1;
    end
    if validLength <= 0
        continue;
    end

    frameCount = frameCount + 1;
    iq = iq(1:validLength);

    buffer = [buffer; iq]; %#ok<AGROW>
    if numel(buffer) > maxBufferSamples
        buffer = buffer(end-maxBufferSamples+1:end);
    end

    searchOffset = 0;
    consumedOffset = 0;

    while searchOffset < numel(buffer)
        previousOffset = searchOffset;

        try
            [bitsData,~,searchOffset,~] = recoverOFDMBits(buffer,searchOffset);
        catch
            break;
        end

        if searchOffset <= previousOffset
            searchOffset = previousOffset + 1;
        end
        if isempty(bitsData)
            continue;
        end

        try
            [cfgMAC,~,decodeStatus] = wlanMPDUDecode(bitsData,SuppressWarnings=true);
        catch
            continue;
        end

        if decodeStatus || ~matches(string(cfgMAC.FrameType),"Beacon")
            continue;
        end

        decodedCount = decodedCount + 1;
        consumedOffset = max(consumedOffset,searchOffset);

        ssid = string(cfgMAC.ManagementConfig.SSID);
        if strlength(ssid) == 0
            ssid = "<SSID oculto>";
        end

        bssid = string(cfgMAC.Address3);

        if ~any(seenBSSIDs == bssid)
            seenBSSIDs(end+1,1) = bssid; %#ok<AGROW>
            fprintf("[%s] SSID: %-32s BSSID: %s\n", ...
                string(datetime("now","Format","HH:mm:ss")),ssid,bssid);
        end
    end

    if consumedOffset > 0 && consumedOffset < numel(buffer)
        buffer = buffer(consumedOffset+1:end);
    elseif consumedOffset >= numel(buffer)
        buffer = complex(zeros(0,1,"single"));
    end

    if toc(lastStatus) >= 5
        fprintf("Estado: frames=%d, overflows=%d, beacons=%d, AP únicos=%d\n", ...
            frameCount,overflowCount,decodedCount,numel(seenBSSIDs));
        lastStatus = tic;
    end
end
end
