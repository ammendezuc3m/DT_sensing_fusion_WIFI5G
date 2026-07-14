function info = parse_albsens_vendor_ie(informationElements)
%PARSE_ALBSENS_VENDOR_IE Locate and parse the ALBSENS Vendor Specific IE.
%
% Expected vendor payload:
%
%   OUI             02 11 22
%   Vendor type     01
%   Magic           "ALBSENS" + 00
%   Version         01
%   Transmitter ID  uint16 big endian
%   Experiment ID   uint16 big endian
%   Packet counter  uint32 big endian
%
% The WLAN Toolbox representation can include the IE ID and length before
% the payload. Therefore, this parser searches for the ALBSENS signature
% instead of assuming that the OUI starts at byte one.

info = struct( ...
    "Found", false, ...
    "Valid", false, ...
    "OUI", "", ...
    "VendorType", NaN, ...
    "Magic", "", ...
    "Version", NaN, ...
    "TransmitterID", NaN, ...
    "ExperimentID", NaN, ...
    "PacketCounter", NaN, ...
    "Reason", "ALBSENS Vendor IE not found");

if isempty(informationElements)
    return;
end

expectedPrefix = uint8([ ...
    hex2dec("02"), ...
    hex2dec("11"), ...
    hex2dec("22"), ...
    hex2dec("01"), ...
    double('ALBSENS'), ...
    0 ...
]);

% Inspect every cell because WLAN Toolbox may store the IE ID, length and
% payload in different cells or include ID/length in the payload itself.
for row = 1:size(informationElements,1)
    for column = 1:size(informationElements,2)

        value = informationElements{row,column};

        if isempty(value)
            continue;
        end

        try
            bytes = uint8(value(:).');
        catch
            continue;
        end

        if numel(bytes) < numel(expectedPrefix)
            continue;
        end

        startIndex = findSubsequence(bytes, expectedPrefix);

        if isempty(startIndex)
            continue;
        end

        % Bytes beginning at the OUI:
        %
        %  1:3   OUI
        %  4     vendor type
        %  5:12  magic
        % 13     version
        % 14:15  transmitter ID
        % 16:17  experiment ID
        % 18:21  packet counter
        if startIndex + 20 > numel(bytes)
            info.Found = true;
            info.Reason = "ALBSENS IE is truncated";
            return;
        end

        payload = bytes(startIndex:startIndex+20);

        info.Found = true;
        info.OUI = upper(join(compose("%02X",payload(1:3)),":"));
        info.VendorType = double(payload(4));

        magicBytes = payload(5:12);
        magicBytes = magicBytes(magicBytes ~= 0);
        info.Magic = string(char(magicBytes));

        info.Version = double(payload(13));
        info.TransmitterID = double(readBE16(payload(14:15)));
        info.ExperimentID = double(readBE16(payload(16:17)));
        info.PacketCounter = double(readBE32(payload(18:21)));

        reasons = strings(0,1);

        if ~isequal(payload(1:3),uint8([2 17 34]))
            reasons(end+1) = "OUI mismatch";
        end

        if payload(4) ~= 1
            reasons(end+1) = "Vendor type mismatch";
        end

        if info.Magic ~= "ALBSENS"
            reasons(end+1) = "Magic mismatch";
        end

        if info.Version ~= 1
            reasons(end+1) = "Version mismatch";
        end

        if info.TransmitterID ~= 1
            reasons(end+1) = "Transmitter ID mismatch";
        end

        if info.ExperimentID ~= 1
            reasons(end+1) = "Experiment ID mismatch";
        end

        info.Valid = isempty(reasons);

        if info.Valid
            info.Reason = "OK";
        else
            info.Reason = join(reasons,"; ");
        end

        return;
    end
end
end


function index = findSubsequence(data,pattern)
index = [];

lastStart = numel(data)-numel(pattern)+1;

for k = 1:lastStart
    if isequal(data(k:k+numel(pattern)-1),pattern)
        index = k;
        return;
    end
end
end


function value = readBE16(bytes)
bytes = uint32(bytes);

value = bitor( ...
    bitshift(bytes(1),8), ...
    bytes(2));
end


function value = readBE32(bytes)
bytes = uint32(bytes);

value = bitor( ...
    bitor(bitshift(bytes(1),24),bitshift(bytes(2),16)), ...
    bitor(bitshift(bytes(3),8),bytes(4)));
end
