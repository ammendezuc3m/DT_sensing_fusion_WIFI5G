function count_my_beacons_usrp()
% Cuenta únicamente los beacons propios recibidos con una USRP B210.
%
% Ejecutar:
% matlab -batch "cd('~/AlbertoDir/DT_sensing_fusion_WIFI5G'); ...
% addpath('tests/wifi/matlab','-begin'); count_my_beacons_usrp"

clc;

%% Configuración USRP
serialNumber     = "34B73C3";
centerFrequency  = 2.462e9;      % Canal WiFi 11
sampleRate       = 20e6;
masterClockRate  = 20e6;
gainDB           = 55;
samplesPerFrame  = 200000;       % 10 ms
channelMapping   = 1;

%% Filtro de nuestros beacons
expectedSSID  = "USRP_CHANNEL11";
expectedBSSID = "021122334455";

%% Añadir helpers del repositorio
repoRoot = fileparts(fileparts(fileparts(fileparts(mfilename("fullpath")))));
helperDir = fullfile(repoRoot,"src","matlab","wifi_sensing");

addpath(helperDir,"-begin");

if exist("recoverOFDMBits","file") == 0
    error("No encuentro recoverOFDMBits.m en: %s",helperDir);
end

%% Crear receptor
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

cleanupObject = onCleanup(@() release(rx)); %#ok<NASGU>

fprintf("=================================================\n");
fprintf("Contador de beacons propios con USRP B210\n");
fprintf("=================================================\n");
fprintf("Serial RX        : %s\n",serialNumber);
fprintf("Frecuencia       : %.3f MHz\n",centerFrequency/1e6);
fprintf("Sample rate      : %.3f Msps\n",sampleRate/1e6);
fprintf("Ganancia RX      : %.1f dB\n",gainDB);
fprintf("Antena física    : RX2\n");
fprintf("SSID esperado    : %s\n",expectedSSID);
fprintf("BSSID esperado   : %s\n",expectedBSSID);
fprintf("Detener          : Ctrl+C\n\n");

%% Estado
ownBeaconCount = 0;
validBeaconCount = 0;
frameCount = 0;
overflowCount = 0;

% Cola pequeña para conservar paquetes cortados entre dos frames.
tailSamples = complex(zeros(0,1,"single"));
tailLength = 20000; % 1 ms a 20 Msps

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

    % Añadir cola del frame anterior.
    processingBuffer = [tailSamples; iq];

    % Guardar solamente una cola pequeña para el siguiente frame.
    if numel(processingBuffer) > tailLength
        tailSamples = processingBuffer(end-tailLength+1:end);
    else
        tailSamples = processingBuffer;
    end

    searchOffset = 0;

    while searchOffset < numel(processingBuffer)
        previousOffset = searchOffset;

        try
            [bitsData,~,searchOffset,~] = ...
                recoverOFDMBits(processingBuffer,searchOffset);
        catch
            break;
        end

        % Protección contra bucle infinito.
        if searchOffset <= previousOffset
            searchOffset = previousOffset + 1;
        end

        if isempty(bitsData)
            continue;
        end

        try
            [cfgMAC,~,decodeStatus] = ...
                wlanMPDUDecode(bitsData,SuppressWarnings=true);
        catch
            continue;
        end

        if decodeStatus
            continue;
        end

        if ~matches(string(cfgMAC.FrameType),"Beacon")
            continue;
        end

        validBeaconCount = validBeaconCount + 1;

        ssid = string(cfgMAC.ManagementConfig.SSID);
        bssid = upper(erase(string(cfgMAC.Address3),":"));

        % Descartar todos los demás AP.
        if ssid ~= expectedSSID
            continue;
        end

        if bssid ~= expectedBSSID
            continue;
        end

        ownBeaconCount = ownBeaconCount + 1;

        sequenceNumber = NaN;

        try
            sequenceNumber = double(cfgMAC.SequenceNumber);
        catch
        end

        fprintf("[%s] Beacon propio recibido: %4d", ...
            string(datetime("now","Format","HH:mm:ss.SSS")), ...
            ownBeaconCount);

        if ~isnan(sequenceNumber)
            fprintf(" | sequence=%d",sequenceNumber);
        end

        fprintf("\n");
    end

    if toc(lastStatus) >= 5
        fprintf( ...
            "Estado: propios=%d | beacons válidos=%d | frames=%d | overflows=%d\n", ...
            ownBeaconCount, ...
            validBeaconCount, ...
            frameCount, ...
            overflowCount);

        lastStatus = tic;
    end
end
end
