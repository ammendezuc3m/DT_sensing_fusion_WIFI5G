function decode_my_beacons_sc16(iqFile, expectedCount)
% DECODE_MY_BEACONS_SC16
% Decodifica offline una captura UHD en formato sc16 interleaved IQ y cuenta
% únicamente los beacons propios.
%
% Ejemplo:
%   decode_my_beacons_sc16( ...
%       "results/wifi_beacon_count/rx_ch11_sc16.dat", 50)
%
% La captura debe haberse realizado a:
%   Fc = 2462 MHz
%   Fs = 20 Msps
%   formato = sc16
%
% Requiere recoverOFDMBits.m del repositorio y WLAN Toolbox.

arguments
    iqFile (1,1) string
    expectedCount (1,1) double {mustBeInteger,mustBePositive} = 50
end

clc;

expectedSSID  = "USRP_CHANNEL11";
expectedBSSID = "021122334455";
sampleRate    = 20e6;

repoRoot = fileparts(fileparts(fileparts(fileparts(mfilename("fullpath")))));
helperDir = fullfile(repoRoot,"src","matlab","wifi_sensing");
addpath(helperDir,"-begin");

if exist("recoverOFDMBits","file") == 0
    error("No encuentro recoverOFDMBits.m en %s",helperDir);
end

if ~isfile(iqFile)
    error("No existe la captura: %s",iqFile);
end

fprintf("Leyendo captura: %s\n",iqFile);

fid = fopen(iqFile,"rb","ieee-le");
if fid < 0
    error("No pude abrir %s",iqFile);
end
cleanupObj = onCleanup(@() fclose(fid)); %#ok<NASGU>

raw = fread(fid,Inf,"int16=>single");
if mod(numel(raw),2) ~= 0
    raw = raw(1:end-1);
end

iq = complex(raw(1:2:end),raw(2:2:end)) / 32768;

fprintf("Muestras IQ: %d\n",numel(iq));
fprintf("Duración    : %.3f s\n\n",numel(iq)/sampleRate);

searchOffset = 0;
ownCount = 0;
validBeacons = 0;
seenSequence = containers.Map("KeyType","double","ValueType","logical");

while searchOffset < numel(iq)
    previousOffset = searchOffset;

    try
        [bitsData,~,searchOffset,~] = recoverOFDMBits(iq,searchOffset);
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

    validBeacons = validBeacons + 1;

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

    % Evita contar dos veces el mismo beacon si el detector encuentra
    % solapamientos sobre la misma trama.
    if ~isnan(sequenceNumber)
        if isKey(seenSequence,sequenceNumber)
            continue;
        end
        seenSequence(sequenceNumber) = true;
    end

    ownCount = ownCount + 1;

    fprintf("Beacon propio %3d",ownCount);
    if ~isnan(sequenceNumber)
        fprintf(" | seq=%d",sequenceNumber);
    end
    fprintf("\n");
end

missing = max(0,expectedCount-ownCount);
prr = 100*ownCount/expectedCount;

fprintf("\n========================================\n");
fprintf("RESULTADO\n");
fprintf("========================================\n");
fprintf("Beacons propios recibidos : %d\n",ownCount);
fprintf("Beacons esperados         : %d\n",expectedCount);
fprintf("Beacons no recibidos      : %d\n",missing);
fprintf("PRR                       : %.2f %%\n",prr);
fprintf("Beacons válidos totales   : %d\n",validBeacons);
end
