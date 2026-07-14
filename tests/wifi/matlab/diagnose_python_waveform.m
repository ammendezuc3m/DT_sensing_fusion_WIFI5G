function diagnose_python_waveform
% Diagnose the exact Python-generated legacy OFDM waveform.

scriptDir = fileparts(mfilename("fullpath"));
repoRoot = fileparts(fileparts(fileparts(scriptDir)));

inputFile = fullfile( ...
    repoRoot, ...
    "results", ...
    "wifi_matlab_rx", ...
    "python_beacon_waveform.mat");

S = load(inputFile);

rx = double(S.capturedData(:));
waveform = double(S.waveform(:));
fs = double(S.sampleRate);

fprintf("Input: %s\n", inputFile);
fprintf("CapturedData samples: %d\n", numel(rx));
fprintf("Waveform samples: %d\n", numel(waveform));
fprintf("Sample rate: %.0f Hz\n", fs);
fprintf("Peak: %.6f\n", max(abs(waveform)));
fprintf("RMS: %.6f\n\n", rms(waveform));

cfg = wlanNonHTConfig( ...
    ChannelBandwidth="CBW20", ...
    MCS=0, ...
    PSDULength=161);

%% 1. WLAN packet detector
packetOffset = wlanPacketDetect(rx, "CBW20");

if isempty(packetOffset)
    fprintf("wlanPacketDetect: NO PACKET DETECTED\n");
else
    fprintf("wlanPacketDetect offset: %d\n", packetOffset);
end

%% 2. Known Python packet position
% export_wifi_beacon_mat.py adds 4000 zero samples first.
pythonPacketStart = 4001;

if numel(rx) < pythonPacketStart + 319
    error("Capture too short.");
end

pythonLSTF = rx(pythonPacketStart:pythonPacketStart+159);
pythonLLTF = rx(pythonPacketStart+160:pythonPacketStart+319);

%% 3. MATLAB reference legacy preamble
referenceLSTF = double(wlanLSTF(cfg));
referenceLLTF = double(wlanLLTF(cfg));

lstfCorrelation = normalizedCorrelation( ...
    pythonLSTF, referenceLSTF);

lltfCorrelation = normalizedCorrelation( ...
    pythonLLTF, referenceLLTF);

fprintf("\nDirect field comparison at known offset:\n");
fprintf("  L-STF normalized correlation: %.9f\n", ...
    lstfCorrelation);
fprintf("  L-LTF normalized correlation: %.9f\n", ...
    lltfCorrelation);

fprintf("  Python L-STF power: %.9e\n", ...
    mean(abs(pythonLSTF).^2));
fprintf("  MATLAB L-STF power: %.9e\n", ...
    mean(abs(referenceLSTF).^2));

fprintf("  Python L-LTF power: %.9e\n", ...
    mean(abs(pythonLLTF).^2));
fprintf("  MATLAB L-LTF power: %.9e\n", ...
    mean(abs(referenceLLTF).^2));

%% 4. Compare possible transformations
fprintf("\nAlternative correlations:\n");

fprintf("  L-STF conjugated: %.9f\n", ...
    normalizedCorrelation(conj(pythonLSTF), referenceLSTF));

fprintf("  L-STF IQ swapped: %.9f\n", ...
    normalizedCorrelation( ...
        imag(pythonLSTF)+1i*real(pythonLSTF), ...
        referenceLSTF));

fprintf("  L-LTF conjugated: %.9f\n", ...
    normalizedCorrelation(conj(pythonLLTF), referenceLLTF));

fprintf("  L-LTF IQ swapped: %.9f\n", ...
    normalizedCorrelation( ...
        imag(pythonLLTF)+1i*real(pythonLLTF), ...
        referenceLLTF));

%% 5. Run MathWorks preamble recovery directly
fprintf("\nrecoverPreamble result:\n");

try
    [status,res] = recoverPreamble(rx, "CBW20", 0);

    fprintf("  Status: %s\n", string(status));

    if isfield(res, "PacketOffset")
        fprintf("  PacketOffset: %d\n", res.PacketOffset);
    end

    if isfield(res, "CFOEstimate")
        fprintf("  CFOEstimate: %.3f Hz\n", res.CFOEstimate);
    end

    if isfield(res, "LLTFSNR")
        fprintf("  LLTF SNR: %.3f dB\n", res.LLTFSNR);
    end

    if isfield(res, "ChanEstNonHT")
        fprintf("  Channel estimate size: ");
        disp(size(res.ChanEstNonHT));
    end
catch ME
    fprintf("  ERROR: %s\n", ME.message);
end

%% 6. Generate a known-valid MATLAB waveform as control
referencePSDU = zeros(8*161,1,"int8");
referenceWaveform = wlanWaveformGenerator(referencePSDU, cfg);

controlRx = [
    complex(zeros(4000,1));
    referenceWaveform;
    complex(zeros(4000,1))
];

controlOffset = wlanPacketDetect(controlRx, "CBW20");

fprintf("\nMATLAB control waveform:\n");

if isempty(controlOffset)
    fprintf("  ERROR: MATLAB reference was not detected\n");
else
    fprintf("  Packet detected at offset: %d\n", controlOffset);
end

try
    [controlBits,controlParams,~,controlRes] = ...
        recoverOFDMBits(controlRx,0);

    fprintf("  Decoded bits: %d\n", numel(controlBits));
    fprintf("  MCS: %g\n", controlParams.MCS);
    fprintf("  PSDU length: %g\n", controlParams.PSDULength);
    fprintf("  failCheck: %g\n", controlParams.failCheck);

    if isfield(controlRes,"PacketOffset")
        fprintf("  recovered offset: %d\n", ...
            controlRes.PacketOffset);
    end
catch ME
    fprintf("  Control recovery error: %s\n", ME.message);
end
end


function value = normalizedCorrelation(a,b)
a = a(:);
b = b(:);

if numel(a) ~= numel(b)
    value = NaN;
    return;
end

value = abs(a'*b)^2 / ...
    ((a'*a)*(b'*b)+eps);

value = real(value);
end
