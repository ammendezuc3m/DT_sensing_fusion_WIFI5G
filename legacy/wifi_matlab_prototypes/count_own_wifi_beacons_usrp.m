function count_own_wifi_beacons_usrp()
% COUNT_OWN_WIFI_BEACONS_USRP
% Captura IQ con una B210, procesa después de terminar la captura y cuenta
% únicamente los beacons del SSID/BSSID configurados.
%
% Ejecución:
%   matlab -batch "count_own_wifi_beacons_usrp"
%
% Procedimiento:
%   1) Ejecutar primero este receptor.
%   2) Cuando aparezca "CAPTURANDO", iniciar el TX finito en el otro PC.
%   3) Al terminar, se muestra recibidos, esperados y PRR.

clc;

%% Configuración de la prueba
serialNumber      = "34B73C3";
centerFrequency   = 2.462e9;       % Canal 11
sampleRate        = 20e6;
masterClockRate   = 20e6;
gainDB            = 55;
channelMapping    = 1;             % RF0; antena físicamente en RX2

expectedSSID      = "USRP_CHANNEL11";
expectedBSSID     = upper(erase("02:11:22:33:44:55",":"));
expectedBeacons   = 50;

% 50 beacons x 102.4 ms = 5.12 s. Se añaden márgenes de arranque y final.
captureSeconds    = 8.0;
samplesPerFrame   = 200000;        % 10 ms
numberOfFrames    = ceil(captureSeconds*sampleRate/samplesPerFrame);
totalCapacity     = numberOfFrames*samplesPerFrame;

%% Añadir helpers del repositorio
repoRoot = fileparts(fileparts(fileparts(fileparts(mfilename("fullpath")))));
helperDir = fullfile(repoRoot,"src","matlab","wifi_sensing");
addpath(helperDir,"-begin");

if exist("recoverOFDMBits","file") == 0
    error("No encuentro recoverOFDMBits.m en %s",helperDir);
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

cleanupObj = onCleanup(@() release(rx)); %#ok<NASGU>

%% Preasignar memoria: 8 s a 20 Msps complex single son ~1.28 GB
iqCapture = complex(zeros(totalCapacity,1,"single"));
writeOffset = 0;
overflowCount = 0;

fprintf("====================================================\n");
fprintf("Contador de beacons propios con USRP B210\n");
fprintf("====================================================\n");
fprintf("SSID esperado      : %s\n",expectedSSID);
fprintf("BSSID esperado     : %s\n",expectedBSSID);
fprintf("Beacons esperados  : %d\n",expectedBeacons);
fprintf("Frecuencia         : %.3f MHz\n",centerFrequency/1e6);
fprintf("Sample rate        : %.3f Msps\n",sampleRate/1e6);
fprintf("Ganancia RX        : %.1f dB\n",gainDB);
fprintf("Captura            : %.1f s\n",captureSeconds);
fprintf("Memoria aproximada : %.2f GB\n\n", ...
    totalCapacity*8/1024^3);

fprintf("CAPTURANDO: inicia ahora el transmisor finito en pablosito.\n\n");

captureTimer = tic;

for frameIndex = 1:numberOfFrames
    [iq,validLength,overflow] = rx();

    if overflow
        overflowCount = overflowCount + 1;
    end

    if validLength > 0
        destination = writeOffset + (1:validLength);
        iqCapture(destination) = iq(1:validLength);
        writeOffset = writeOffset + validLength;
    end

    if mod(frameIndex,100) == 0
        fprintf("Captura: %.1f/%.1f s | muestras=%d | overflows=%d\n", ...
            toc(captureTimer),captureSeconds,writeOffset,overflowCount);
    end
end

release(rx);
iqCapture = iqCapture(1:writeOffset);

fprintf("\nCaptura terminada: %d muestras, %d overflows.\n", ...
    writeOffset,overflowCount);
fprintf("Procesando fuera de línea...\n\n");

%% Decodificación offline
searchOffset = 0;
ownBeaconCount = 0;
allValidBeaconCount = 0;
sequenceNumbers = nan(0,1);
packetOffsets = nan(0,1);

while searchOffset < numel(iqCapture)
    previousOffset = searchOffset;

    try
        [bitsData,~,searchOffset,res] = ...
            recoverOFDMBits(iqCapture,searchOffset);
    catch err
        fprintf(2,"recoverOFDMBits terminó con: %s\n",err.message);
        break;
    end

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

    if decodeStatus || ~matches(string(cfgMAC.FrameType),"Beacon")
        continue;
    end

    allValidBeaconCount = allValidBeaconCount + 1;

    ssid = string(cfgMAC.ManagementConfig.SSID);
    bssid = upper(erase(string(cfgMAC.Address3),":"));

    if ssid ~= expectedSSID || bssid ~= expectedBSSID
        continue;
    end

    ownBeaconCount = ownBeaconCount + 1;

    sequenceNumber = NaN;
    if isprop(cfgMAC,"SequenceNumber")
        sequenceNumber = double(cfgMAC.SequenceNumber);
    end

    sequenceNumbers(end+1,1) = sequenceNumber; %#ok<AGROW>

    packetOffset = NaN;
    if isstruct(res) && isfield(res,"PacketOffset")
        packetOffset = double(res.PacketOffset);
    end
    packetOffsets(end+1,1) = packetOffset; %#ok<AGROW>

    fprintf("Beacon propio %3d/%d",ownBeaconCount,expectedBeacons);
    if ~isnan(sequenceNumber)
        fprintf(" | seq=%d",sequenceNumber);
    end
    if ~isnan(packetOffset)
        fprintf(" | t=%.6f s",packetOffset/sampleRate);
    end
    fprintf("\n");
end

%% Resultados
prr = 100*ownBeaconCount/expectedBeacons;
missing = max(0,expectedBeacons-ownBeaconCount);

fprintf("\n====================================================\n");
fprintf("RESULTADO\n");
fprintf("====================================================\n");
fprintf("Beacons propios recibidos : %d\n",ownBeaconCount);
fprintf("Beacons esperados         : %d\n",expectedBeacons);
fprintf("Beacons no recibidos      : %d\n",missing);
fprintf("PRR                       : %.2f %%\n",prr);
fprintf("Beacons válidos totales   : %d\n",allValidBeaconCount);
fprintf("Overflows durante captura : %d\n",overflowCount);

if ownBeaconCount == expectedBeacons
    fprintf("Resultado: han llegado todos los beacons.\n");
elseif ownBeaconCount < expectedBeacons
    fprintf("Resultado: faltan %d beacons.\n",missing);
else
    fprintf(["Resultado: se contaron más beacons de los esperados. " ...
        "Comprueba que no quedó otro TX activo o que no se repitió la prueba.\n"]);
end

%% Guardar resultado
outputDir = fullfile(repoRoot,"results","wifi_beacon_count");
if ~isfolder(outputDir)
    mkdir(outputDir);
end

timestamp = string(datetime("now","Format","yyyyMMdd_HHmmss"));
resultPath = fullfile(outputDir,"count_" + timestamp + ".mat");

save(resultPath, ...
    "expectedSSID","expectedBSSID","expectedBeacons", ...
    "ownBeaconCount","prr","missing","overflowCount", ...
    "sequenceNumbers","packetOffsets");

fprintf("Resultado guardado en: %s\n",resultPath);
end
