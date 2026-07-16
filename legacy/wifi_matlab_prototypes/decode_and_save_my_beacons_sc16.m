function decode_and_save_my_beacons_sc16(iqFile, expectedCount)
% DECODE_AND_SAVE_MY_BEACONS_SC16
% Procesa una captura sc16 por bloques, filtra únicamente nuestros beacons,
% elimina duplicados producidos por el solapamiento entre bloques y guarda
% cada beacon único en un archivo MAT.
%
% Ejemplo:
%   decode_and_save_my_beacons_sc16( ...
%       "results/wifi_beacon_count/rx_ch11_sc16_v2.dat", 50)
%
% Un beacon se considera propio cuando:
%   - wlanMPDUDecode devuelve FCS/MPDU válido;
%   - FrameType == Beacon;
%   - SSID == USRP_CHANNEL11;
%   - BSSID == 021122334455.
%
% La clave única usada es SequenceNumber. El solapamiento puede hacer que
% el mismo beacon se encuentre en dos bloques, pero solo se guarda una vez.

arguments
    iqFile (1,1) string
    expectedCount (1,1) double {mustBeInteger,mustBePositive} = 50
end

clc;

%% Configuración conocida del TX
expectedSSID  = "USRP_CHANNEL11";
expectedBSSID = "021122334455";
sampleRate    = 20e6;

chunkComplexSamples   = 2000000; % 100 ms
overlapComplexSamples = 200000;  % 10 ms

%% Rutas
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

fprintf("====================================================\n");
fprintf("Decodificación y guardado de beacons propios\n");
fprintf("====================================================\n");
fprintf("Archivo             : %s\n",iqFile);
fprintf("Muestras IQ         : %d\n",totalComplexSamples);
fprintf("Duración            : %.3f s\n",totalComplexSamples/sampleRate);
fprintf("SSID esperado       : %s\n",expectedSSID);
fprintf("BSSID esperado      : %s\n",expectedBSSID);
fprintf("Secuencias esperadas: 0..%d\n\n",expectedCount-1);

fid = fopen(iqFile,"rb","ieee-le");
if fid < 0
    error("No pude abrir %s",iqFile);
end
cleanupFile = onCleanup(@() fclose(fid)); %#ok<NASGU>

%% Estructura de salida
emptyRecord = struct( ...
    "SequenceNumber",NaN, ...
    "SSID","", ...
    "BSSID","", ...
    "FrameType","", ...
    "MPDUBits",false(0,1), ...
    "MPDUBytes",uint8([]), ...
    "ChunkIndex",NaN, ...
    "ApproxSampleOffset",NaN, ...
    "ApproxTimeSeconds",NaN);

records = repmat(emptyRecord,0,1);

% SequenceNumber -> índice de records.
seenSequence = containers.Map("KeyType","double","ValueType","double");

tail = complex(zeros(0,1,"single"));
chunkIndex = 0;
processedNewSamples = 0;
allValidBeacons = 0;
ownDetectionsIncludingDuplicates = 0;
duplicateDetections = 0;

while true
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

    processingBuffer = [tail; currentIQ];
    clear currentIQ;

    % Posición global aproximada del primer sample de processingBuffer.
    globalBufferStart = max(0,processedNewSamples-numel(tail));
    processedNewSamples = processedNewSamples + newSamplesThisChunk;

    searchOffset = 0;

    while searchOffset < numel(processingBuffer)
        previousOffset = searchOffset;

        try
            [bitsData,~,searchOffset,res] = ...
                recoverOFDMBits(processingBuffer,searchOffset);
        catch
            % Un candidato incompleto/corrupto no debe abortar el fichero.
            searchOffset = previousOffset + 1;
            continue;
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

        allValidBeacons = allValidBeacons + 1;

        ssid = string(cfgMAC.ManagementConfig.SSID);
        bssid = upper(erase(string(cfgMAC.Address3),":"));

        if ssid ~= expectedSSID || bssid ~= expectedBSSID
            continue;
        end

        ownDetectionsIncludingDuplicates = ...
            ownDetectionsIncludingDuplicates + 1;

        sequenceNumber = NaN;
        try
            sequenceNumber = double(cfgMAC.SequenceNumber);
        catch
        end

        if isnan(sequenceNumber)
            fprintf("AVISO: beacon propio sin SequenceNumber; descartado.\n");
            continue;
        end

        if isKey(seenSequence,sequenceNumber)
            duplicateDetections = duplicateDetections + 1;
            fprintf("Duplicado ignorado | seq=%d | bloque=%d\n", ...
                sequenceNumber,chunkIndex);
            continue;
        end

        mpduBits = logical(bitsData(:));
        usableBits = floor(numel(mpduBits)/8)*8;

        if usableBits > 0
            % Cada fila contiene los 8 bits de un byte.
            % Los bits WLAN se entregan LSB-first.
            bitMatrix = reshape( ...
                double(mpduBits(1:usableBits)), ...
                8,[]).';

            bitWeights = 2.^(0:7);
            mpduBytes = uint8(bitMatrix * bitWeights.');
        else
            mpduBytes = uint8([]);
        end

        localPacketOffset = NaN;
        try
            if isstruct(res) && isfield(res,"PacketOffset")
                localPacketOffset = double(res.PacketOffset);
            end
        catch
        end

        if isnan(localPacketOffset)
            approximateGlobalOffset = globalBufferStart + previousOffset;
        else
            approximateGlobalOffset = ...
                globalBufferStart + localPacketOffset;
        end

        rec = emptyRecord;
        rec.SequenceNumber = sequenceNumber;
        rec.SSID = ssid;
        rec.BSSID = bssid;
        rec.FrameType = string(cfgMAC.FrameType);
        rec.MPDUBits = mpduBits;
        rec.MPDUBytes = mpduBytes;
        rec.ChunkIndex = chunkIndex;
        rec.ApproxSampleOffset = approximateGlobalOffset;
        rec.ApproxTimeSeconds = approximateGlobalOffset/sampleRate;

        records(end+1,1) = rec; %#ok<AGROW>
        seenSequence(sequenceNumber) = numel(records);

        fprintf("Beacon único %3d | seq=%d | t≈%.6f s | bytes=%d\n", ...
            numel(records),sequenceNumber, ...
            rec.ApproxTimeSeconds,numel(mpduBytes));
    end

    if numel(processingBuffer) > overlapComplexSamples
        tail = processingBuffer(end-overlapComplexSamples+1:end);
    else
        tail = processingBuffer;
    end
    clear processingBuffer;

    fprintf("Bloque %3d | progreso=%6.2f %% | únicos=%d | duplicados=%d\n", ...
        chunkIndex, ...
        100*processedNewSamples/totalComplexSamples, ...
        numel(records),duplicateDetections);
end

%% Ordenar por secuencia y analizar pérdidas
if isempty(records)
    uniqueSequences = zeros(0,1);
else
    [~,order] = sort([records.SequenceNumber]);
    records = records(order);
    uniqueSequences = [records.SequenceNumber].';
end

expectedSequences = (0:expectedCount-1).';
missingSequences = setdiff(expectedSequences,uniqueSequences);
unexpectedSequences = setdiff(uniqueSequences,expectedSequences);

uniqueCount = numel(uniqueSequences);
prr = 100*uniqueCount/expectedCount;

%% Guardar
outputDir = fullfile(repoRoot,"results","wifi_beacon_count");
if ~isfolder(outputDir)
    mkdir(outputDir);
end

timestamp = string(datetime("now","Format","yyyyMMdd_HHmmss"));
matPath = fullfile(outputDir,"decoded_beacons_" + timestamp + ".mat");

% Construir columnas explícitamente con igual número de filas.
numberOfRecords = numel(records);

sequenceColumn = reshape( ...
    [records.SequenceNumber],[],1);

timeColumn = reshape( ...
    [records.ApproxTimeSeconds],[],1);

chunkColumn = reshape( ...
    [records.ChunkIndex],[],1);

ssidColumn = reshape( ...
    string({records.SSID}),[],1);

bssidColumn = reshape( ...
    string({records.BSSID}),[],1);

summaryTable = table( ...
    sequenceColumn, ...
    timeColumn, ...
    chunkColumn, ...
    ssidColumn, ...
    bssidColumn, ...
    'VariableNames',{ ...
        'SequenceNumber', ...
        'ApproxTimeSeconds', ...
        'ChunkIndex', ...
        'SSID', ...
        'BSSID'});

save(matPath, ...
    "records","summaryTable", ...
    "expectedSSID","expectedBSSID","expectedCount", ...
    "uniqueSequences","missingSequences","unexpectedSequences", ...
    "ownDetectionsIncludingDuplicates","duplicateDetections", ...
    "allValidBeacons","prr","iqFile");

csvPath = replace(matPath,".mat",".csv");
writetable(summaryTable,csvPath);

%% Resultado
fprintf("\n====================================================\n");
fprintf("RESULTADO CORREGIDO\n");
fprintf("====================================================\n");
fprintf("Detecciones propias brutas : %d\n", ...
    ownDetectionsIncludingDuplicates);
fprintf("Duplicados por solapamiento: %d\n",duplicateDetections);
fprintf("Beacons propios únicos     : %d\n",uniqueCount);
fprintf("Beacons esperados          : %d\n",expectedCount);
fprintf("PRR único                  : %.2f %%\n",prr);
fprintf("Beacons válidos totales    : %d\n",allValidBeacons);

fprintf("Secuencias recibidas       : %s\n",mat2str(uniqueSequences.'));
fprintf("Secuencias ausentes        : %s\n",mat2str(missingSequences.'));
fprintf("Secuencias inesperadas     : %s\n",mat2str(unexpectedSequences.'));

fprintf("MAT guardado               : %s\n",matPath);
fprintf("CSV guardado               : %s\n",csvPath);

if isempty(missingSequences) && isempty(unexpectedSequences)
    fprintf("Conclusión: se recibieron exactamente las secuencias esperadas.\n");
elseif ~isempty(missingSequences)
    fprintf("Conclusión: faltan %d secuencias únicas.\n",numel(missingSequences));
else
    fprintf("Conclusión: hay secuencias fuera del rango esperado.\n");
end
end
