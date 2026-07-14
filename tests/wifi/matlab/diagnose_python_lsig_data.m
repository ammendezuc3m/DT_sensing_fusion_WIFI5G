function diagnose_python_lsig_data
% Diagnose L-SIG and DATA fields of the Python-generated non-HT waveform.

scriptDir = fileparts(mfilename("fullpath"));
repoRoot = fileparts(fileparts(fileparts(scriptDir)));

inputFile = fullfile( ...
    repoRoot, ...
    "results", ...
    "wifi_matlab_rx", ...
    "python_beacon_waveform.mat");

S = load(inputFile);

rx = double(S.capturedData(:));
pythonWaveform = double(S.waveform(:));

cbw = "CBW20";
sampleRate = 20e6;

fprintf("Input file: %s\n", inputFile);
fprintf("Python waveform samples: %d\n\n", numel(pythonWaveform));

%% Recover preamble
[preambleStatus,res] = recoverPreamble(rx,cbw,0);

fprintf("Preamble status: %s\n", string(preambleStatus));

if ~matches(preambleStatus,"Success")
    error("Preamble recovery failed.");
end

fprintf("Packet offset: %d\n", res.PacketOffset);
fprintf("CFO estimate: %.3f Hz\n", res.CFOEstimate);
fprintf("Channel estimate size: ");
disp(size(res.ChanEstNonHT));

%% Synchronize and normalize exactly as recoverOFDMBits does
syncData = rx(res.PacketOffset+1:end)./sqrt(res.LSTFPower);
syncData = frequencyOffset( ...
    syncData, ...
    sampleRate, ...
    -res.CFOEstimate);

cfgProbe = wlanNonHTConfig(ChannelBandwidth=cbw);
indProbe = wlanFieldIndices(cfgProbe);

%% Direct L-SIG recovery
pythonLSIG = syncData(indProbe.LSIG(1):indProbe.LSIG(2));

[lsigBits,failCheck] = wlanLSIGRecover( ...
    pythonLSIG, ...
    res.ChanEstNonHT, ...
    res.NoiseEstNonHT, ...
    cbw);

fprintf("\nL-SIG recovery\n");
fprintf("  failCheck: %d\n", failCheck);
fprintf("  bits: ");
fprintf("%d", lsigBits);
fprintf("\n");

rateBits = double(lsigBits(1:4)).';
reservedBit = double(lsigBits(5));
lengthBits = double(lsigBits(6:17));
parityBit = double(lsigBits(18));
tailBits = double(lsigBits(19:24)).';

fprintf("  RATE bits: ");
fprintf("%d", rateBits);
fprintf("\n");

fprintf("  Reserved: %d\n", reservedBit);

psduLength = double(bit2int(lsigBits(6:17),12,0));
fprintf("  PSDU length: %d bytes\n", psduLength);

fprintf("  Parity bit: %d\n", parityBit);

fprintf("  Tail bits: ");
fprintf("%d", tailBits);
fprintf("\n");

expectedParity = mod(sum(double(lsigBits(1:17))),2);
fprintf("  Calculated parity: %d\n", expectedParity);

%% Compare Python L-SIG against MATLAB reference L-SIG
cfgReference = wlanNonHTConfig( ...
    ChannelBandwidth="CBW20", ...
    MCS=0, ...
    PSDULength=161);

referenceBits = zeros(8*161,1,"int8");
referenceWaveform = wlanWaveformGenerator(referenceBits,cfgReference);
referenceIndices = wlanFieldIndices(cfgReference);

referenceLSIG = double(referenceWaveform( ...
    referenceIndices.LSIG(1):referenceIndices.LSIG(2)));

pythonLSIGRaw = pythonWaveform(321:400);

lsigCorr = normalizedCorrelation(pythonLSIGRaw,referenceLSIG);

fprintf("\nL-SIG comparison with MATLAB reference\n");
fprintf("  Normalized correlation: %.12f\n", lsigCorr);
fprintf("  Python L-SIG power: %.9e\n", mean(abs(pythonLSIGRaw).^2));
fprintf("  MATLAB L-SIG power: %.9e\n", mean(abs(referenceLSIG).^2));

%% Stop here if L-SIG is invalid
if failCheck
    fprintf("\nRESULT: L-SIG IS INVALID\n");
    return;
end

%% Obtain MCS using the same logic as recoverOFDMBits
rate = double(bit2int(lsigBits(1:3),3));

if rate <= 1
    mcs = rate + 6;
else
    mcs = mod(rate,6);
end

fprintf("\nDecoded MCS: %d\n",mcs);

cfgData = wlanNonHTConfig( ...
    ChannelBandwidth=cbw, ...
    MCS=mcs, ...
    PSDULength=psduLength);

nonHTDataIndices = wlanFieldIndices(cfgData,"NonHT-Data");

if nonHTDataIndices(2) > numel(syncData)
    fprintf("\nRESULT: PACKET IS TRUNCATED\n");
    fprintf("Needed through sample: %d\n",nonHTDataIndices(2));
    fprintf("Available samples: %d\n",numel(syncData));
    return;
end

%% Recover DATA
nonHTData = syncData( ...
    nonHTDataIndices(1):nonHTDataIndices(2));

recoveredBits = wlanNonHTDataRecover( ...
    nonHTData, ...
    res.ChanEstNonHT, ...
    res.NoiseEstNonHT, ...
    cfgData);

fprintf("\nDATA recovery\n");
fprintf("  Recovered bits: %d\n",numel(recoveredBits));
fprintf("  Expected bits: %d\n",8*psduLength);

if isempty(recoveredBits)
    fprintf("RESULT: DATA RECOVERY RETURNED NO BITS\n");
    return;
end

%% Decode MPDU
[cfgMAC,~,decodeStatus] = wlanMPDUDecode( ...
    recoveredBits, ...
    SuppressWarnings=true);

fprintf("  MPDU decode status: %d\n",decodeStatus);

if decodeStatus
    fprintf("\nRESULT: DATA BITS RECOVERED, BUT MPDU/FCS FAILED\n");
    return;
end

fprintf("  Frame type: %s\n",string(cfgMAC.FrameType));

if matches(string(cfgMAC.FrameType),"Beacon")
    fprintf("  SSID: %s\n",string(cfgMAC.ManagementConfig.SSID));
    fprintf("  Address2: %s\n",string(cfgMAC.Address2));
    fprintf("  Address3: %s\n",string(cfgMAC.Address3));

    vendor = parse_albsens_vendor_ie( ...
        cfgMAC.ManagementConfig.InformationElements);

    fprintf("  Vendor found: %d\n",vendor.Found);
    fprintf("  Vendor valid: %d\n",vendor.Valid);
    fprintf("  Vendor reason: %s\n",vendor.Reason);

    if vendor.Found
        fprintf("  Packet counter: %.0f\n",vendor.PacketCounter);
    end
end

fprintf("\nRESULT: PHY AND MPDU DECODE COMPLETED\n");
end


function value = normalizedCorrelation(a,b)
a = a(:);
b = b(:);

value = abs(a'*b)^2 / ...
    ((a'*a)*(b'*b)+eps);

value = real(value);
end
