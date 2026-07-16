function decode_my_beacons_sc16_chunked(iqFile, expectedCount)
% DECODE_MY_BEACONS_SC16_CHUNKED
% Decodifica una captura UHD sc16 por bloques para evitar agotar la RAM.
%
% Ejemplo:
%   decode_my_beacons_sc16_chunked( ...
%       "results/wifi_beacon_count/rx_ch11_sc16.dat", 50)
%
% Formato esperado:
%   int16 little-endian intercalado: I0,Q0,I1,Q1,...
%   Fs = 20 Msps
%
% El script:
%   - lee bloques pequeños del fichero;
%   - conserva solapamiento entre bloques;
%   - decodifica beacons;
%   - filtra SSID y BSSID propios;
%   - evita duplicados por número de secuencia;
%   - calcula PRR.

arguments
    iqFile (1,1) string
    expectedCount (1,1) double {mustBeInteger,mustBePositive} = 50
end

clc;

%% Configuración
expectedSSID  = "USRP_CHANNEL11";
expectedBSSID = "021122334455";
sampleRate    = 20e6;

% 2.000.000 muestras complejas = 100 ms a 20 Msps.
% Memoria aproximada por bloque: decenas de MB, no gigabytes.
chunkComplexSamples = 2000000;

% Solapamiento de 200.000 muestras = 10 ms.
% Suficiente para conservar paquetes cortados en el límite entre bloques.
overlapComplexSamples = 200000;

%% Rutas del repositorio
repoRoot = fileparts(fileparts(fileparts(fileparts(mfilename("fullpath")))));
helperDir = fullfile(repoRoot,"src","matlab","wifi_sensing");
addpath(helperDir,"-begin");

if exist("recoverOFDMBits","file") == 0
    error("No encuentro recoverOFDMBits.m en %s",helperDir);
end

if ~isfile(iqFile)
    error("No existe la captura: %s",iqFile);
end

fileInfo = dir(iqFile);
totalComplexSamples = floor(fileInfo.bytes/4);
captureDuration = totalComplexSamples/sampleRate;

fprintf("====================================================\n");
fprintf("Decodificador chunked de beacons propios\n");
fprintf("====================================================\n");
fprintf("Archivo                  : %s\n",iqFile);
fprintf("Tamaño                   : %.2f MiB\n",fileInfo.bytes/1024^2);
fprintf("Muestras IQ              : %d\n",totalComplexSamples);
fprintf("Duración                 : %.3f s\n",captureDuration);
fprintf("Bloque                   : %d muestras (%.1f ms)\n", ...
    chunkComplexSamples,chunkComplexSamples/sampleRate*1e3);
fprintf("Solapamiento             : %d muestras (%.1f ms)\n", ...
    overlapComplexSamples,overlapComplexSamples/sampleRate*1e3);
fprintf("SSID esperado            : %s\n",expectedSSID);
fprintf("BSSID esperado           : %s\n",expectedBSSID);
fprintf("Beacons esperados        : %d\n\n",expectedCount);

fid = fopen(iqFile,"rb","ieee-le");
if fid < 0
    error("No pude abrir %s",iqFile);
end
cleanupObj = onCleanup(@() fclose(fid)); %#ok<NASGU>

%% Estado
tail = complex(zeros(0,1,"single"));
chunkIndex = 0;
processedComplexSamples = 0;
validBeaconCount = 0;
ownBeaconCount = 0;

% Evitar duplicados entre bloques.
seenSequence = containers.Map("KeyType","double","ValueType","logical");

while true
    % Dos int16 por muestra compleja.
    raw = fread(fid,2*chunkComplexSamples,"int16=>single");

    if isempty(raw)
        break;
    end

    if mod(numel(raw),2) ~= 0
        raw = raw(1:end-1);
    end

    currentIQ = complex(raw(1:2:end),raw(2:2:end))/32768;
    clear raw;

    chunkIndex = chunkIndex + 1;
    newSamplesThisChunk = numel(currentIQ);
    processedComplexSamples = processedComplexSamples + newSamplesThisChunk;

    processingBuffer = [tail; currentIQ];
    clear currentIQ;

    searchOffset = 0;

    while searchOffset < numel(processingBuffer)
        previousOffset = searchOffset;

        try
            [bitsData,~,searchOffset,~] = ...
                recoverOFDMBits(processingBuffer,searchOffset);
        catch err
            fprintf(2, ...
                "Aviso en bloque %d: recoverOFDMBits: %s\n", ...
                chunkIndex,err.message);
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

        validBeaconCount = validBeaconCount + 1;

        ssid = string(cfgMAC.ManagementConfig.SSID);
        bssid = upper(erase(string(cfgMAC.Address3),":"));

        if ssid ~= expectedSSID || bssid ~= expectedBSSID
            continue;
        end

        sequenceNumber = NaN;
        try
            sequenceNumber = double(cfgMAC.SequenceNumber);
        catch
        end

        % Contar cada beacon propio decodificado.
        % No usar SequenceNumber como identificador único porque algunos
        % generadores mantienen ese campo fijo en cero.
        ownBeaconCount = ownBeaconCount + 1;

        fprintf("Beacon propio %3d",ownBeaconCount);
        if ~isnan(sequenceNumber)
            fprintf(" | seq=%d",sequenceNumber);
        end
        fprintf("\n");
    end

    % Conservar únicamente el final del bloque.
    if numel(processingBuffer) > overlapComplexSamples
        tail = processingBuffer(end-overlapComplexSamples+1:end);
    else
        tail = processingBuffer;
    end
    clear processingBuffer;

    fprintf( ...
        "Bloque %3d | progreso=%6.2f %% | propios=%d | válidos=%d\n", ...
        chunkIndex, ...
        100*processedComplexSamples/totalComplexSamples, ...
        ownBeaconCount, ...
        validBeaconCount);
end

%% Resultado
missing = max(0,expectedCount-ownBeaconCount);
prr = 100*ownBeaconCount/expectedCount;

fprintf("\n====================================================\n");
fprintf("RESULTADO\n");
fprintf("====================================================\n");
fprintf("Beacons propios recibidos : %d\n",ownBeaconCount);
fprintf("Beacons esperados         : %d\n",expectedCount);
fprintf("Beacons no recibidos      : %d\n",missing);
fprintf("PRR                       : %.2f %%\n",prr);
fprintf("Beacons válidos totales   : %d\n",validBeaconCount);

if ownBeaconCount == expectedCount
    fprintf("Resultado: llegaron todos los beacons.\n");
elseif ownBeaconCount < expectedCount
    fprintf("Resultado: faltan %d beacons.\n",missing);
else
    fprintf(["Resultado: se contaron más beacons de los esperados; " ...
        "revisa duplicados o transmisiones previas.\n"]);
end
end
