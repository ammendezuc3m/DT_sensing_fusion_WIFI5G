function test_python_waveform
% Decode the exact beacon waveform generated in Python, without RF.

scriptDir = fileparts(mfilename("fullpath"));
repoRoot = fileparts(fileparts(fileparts(scriptDir)));

inputFile = fullfile( ...
    repoRoot, ...
    "results", ...
    "wifi_matlab_rx", ...
    "python_beacon_waveform.mat");

fprintf("Input: %s\n", inputFile);

if ~isfile(inputFile)
    error("Input file not found: %s", inputFile);
end

S = load(inputFile);
rx = S.capturedData;

fprintf("Samples: %d\n", size(rx,1));
fprintf("Peak amplitude: %.6f\n", max(abs(rx),[],"all"));
fprintf("RMS amplitude: %.6f\n", rms(rx,"all"));

searchOffset = 0;
decodedPackets = 0;

while searchOffset < size(rx,1)
    previousOffset = searchOffset;

    [decBits, decParams, searchOffset, res] = ...
        recoverOFDMBits(rx, searchOffset);

    if searchOffset <= previousOffset
        searchOffset = previousOffset + 80;
    end

    if isempty(decBits)
        continue;
    end

    decodedPackets = decodedPackets + 1;

    fprintf("\nPHY packet decoded\n");
    fprintf("  Packet offset: %d\n", res.PacketOffset);
    fprintf("  CFO estimate: %.3f Hz\n", res.CFOEstimate);
    fprintf("  MCS: %d\n", decParams.MCS);
    fprintf("  PSDU length: %d bytes\n", decParams.PSDULength);
    fprintf("  L-SIG failCheck: %d\n", decParams.failCheck);

    [cfgMAC, mpduPayload, decodeStatus] = wlanMPDUDecode( ...
        decBits, ...
        SuppressWarnings=true);

    fprintf("  MPDU decode status: %d\n", decodeStatus);

    if decodeStatus
        fprintf("  RESULT: PHY recovered, but MPDU/FCS failed\n");
        continue;
    end

    fprintf("  Frame type: %s\n", string(cfgMAC.FrameType));

    if matches(string(cfgMAC.FrameType), "Beacon")
        fprintf("  SSID: %s\n", ...
            string(cfgMAC.ManagementConfig.SSID));
        fprintf("  Address1: %s\n", string(cfgMAC.Address1));
        fprintf("  Address2: %s\n", string(cfgMAC.Address2));
        fprintf("  Address3/BSSID: %s\n", string(cfgMAC.Address3));

        vendor = parse_albsens_vendor_ie( ...
            cfgMAC.ManagementConfig.InformationElements);

        fprintf("  Vendor IE found: %d\n", vendor.Found);
        fprintf("  Vendor IE valid: %d\n", vendor.Valid);
        fprintf("  Vendor reason: %s\n", vendor.Reason);

        if vendor.Found
            fprintf("  Packet counter: %.0f\n", vendor.PacketCounter);
        end

        if isfield(res, "ChanEstNonHT")
            fprintf("  CSI size: ");
            disp(size(res.ChanEstNonHT));
        end

        if vendor.Valid
            fprintf("\nRESULT: PYTHON WAVEFORM IS VALID\n");
        else
            fprintf("\nRESULT: BEACON DECODED, VENDOR IE INVALID\n");
        end
    else
        fprintf("\nRESULT: MPDU DECODED, BUT IT IS NOT A BEACON\n");
    end
end

if decodedPackets == 0
    fprintf("\nRESULT: MATLAB COULD NOT DECODE THE PYTHON WAVEFORM\n");
end
end
