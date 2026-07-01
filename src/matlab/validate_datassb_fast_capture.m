%% Validate fast dataSSB capture
% This script validates that saved dataSSB snapshots still contain a
% properly aligned SSB.
%
% It checks:
%   1) visual appearance,
%   2) PSS correlation,
%   3) SSS correlation and detected NCellID,
%   4) optional PBCH/BCH decode on a subset.

clear;
clc;

%% Paths
scriptDir = fileparts(mfilename("fullpath"));
projectRoot = fileparts(fileparts(scriptDir));

projectMatlabPath = fullfile(projectRoot, "src", "matlab");

exampleRoot = fullfile( ...
    projectRoot, ...
    "NRSSBCaptureUsingSDRExample-20260619T104542Z-3-001", ...
    "NRSSBCaptureUsingSDRExample");

addpath(genpath(projectMatlabPath));
addpath(genpath(exampleRoot));

%% Load latest fixed-frequency dynamic-timing file
dataDir = fullfile(projectRoot, "data", "fast_datassb");
files = dir(fullfile(dataDir, "datassb_fixedfreq_dynamictiming_fast_*.mat"));

if isempty(files)
    error("No fixed-frequency dynamic-timing dataSSB file found in %s", dataDir);
end

[~, newestIdx] = max([files.datenum]);
matFile = fullfile(files(newestIdx).folder, files(newestIdx).name);

fprintf("Loading:\n%s\n", matFile);

load(matFile, "dataSSB", "validMask", "cfg", "syncState", "timingOffsets");

validIdx = find(validMask);

fprintf("\n=== Loaded data ===\n");
fprintf("dataSSB size: %s\n", mat2str(size(dataSSB)));
fprintf("Valid captures: %d\n", numel(validIdx));
fprintf("Fixed frequency offset: %.2f Hz\n", syncState.FrequencyOffsetHz);
fprintf("Fixed NID2: %d\n", syncState.NID2);

%% SSB position inside 30-RB grid
ssbFreqOrigin = 12 * (cfg.DemodRB - cfg.NRBSSB) / 2 + 1;
ssbRows = ssbFreqOrigin:(ssbFreqOrigin + cfg.NRBSSB * 12 - 1);

fprintf("\nSSB rows inside dataSSB: %d:%d\n", ssbRows(1), ssbRows(end));
fprintf("Candidate SSB symbol windows: 1:4, 2:5, 3:6\n");

%% Visual check
nPlot = min(16, numel(validIdx));
plotIdx = validIdx(round(linspace(1, numel(validIdx), nPlot)));

figure("Name", "Fast dataSSB visual check");
tiledlayout(4, 4, "TileSpacing", "compact");

for k = 1:nPlot
    idx = plotIdx(k);

    nexttile;
    imagesc(abs(dataSSB(:, :, idx)));
    axis xy;
    title(sprintf("Capture %d", idx));
    xlabel("OFDM symbol");
    ylabel("Subcarrier");

    hold on;
    rectangle( ...
        "Position", [1.5, ssbFreqOrigin - 0.5, 4, cfg.NRBSSB * 12], ...
        "EdgeColor", "r", ...
        "LineWidth", 1.0);
    hold off;
end

%% Correlation check
NID2 = syncState.NID2;

pssRef = nrPSS(NID2);
pssIndices = nrPSSIndices;
sssIndices = nrSSSIndices;

sssRefMat = zeros(127, 336);
for nid1 = 0:335
    ncellidCandidate = 3 * nid1 + NID2;
    sssRefMat(:, nid1 + 1) = nrSSS(ncellidCandidate);
end

nCheck = min(200, numel(validIdx));
checkIdx = validIdx(round(linspace(1, numel(validIdx), nCheck)));

bestColStart = nan(nCheck, 1);
bestPSSCorr = nan(nCheck, 1);
bestSSSCorr = nan(nCheck, 1);
NID1Log = nan(nCheck, 1);
NCellIDLog = nan(nCheck, 1);

for m = 1:nCheck
    idx = checkIdx(m);

    localBestPSS = -inf;
    localBestSSS = -inf;
    localBestStart = NaN;
    localBestNID1 = NaN;

    for colStart = 1:3
        rxGridSSB = double(dataSSB(ssbRows, colStart:(colStart + 3), idx));

        pssRx = nrExtractResources(pssIndices, rxGridSSB);
        pssCorr = abs(pssRef' * pssRx) / (norm(pssRef) * norm(pssRx) + eps);

        sssRx = nrExtractResources(sssIndices, rxGridSSB);
        sssCorrs = abs(sssRefMat' * sssRx) ./ ((vecnorm(sssRefMat).') * norm(sssRx) + eps);

        [sssCorr, nid1Idx] = max(sssCorrs);
        nid1 = nid1Idx - 1;

        score = pssCorr + sssCorr;

        if score > (localBestPSS + localBestSSS)
            localBestPSS = pssCorr;
            localBestSSS = sssCorr;
            localBestStart = colStart;
            localBestNID1 = nid1;
        end
    end

    bestColStart(m) = localBestStart;
    bestPSSCorr(m) = localBestPSS;
    bestSSSCorr(m) = localBestSSS;
    NID1Log(m) = localBestNID1;
    NCellIDLog(m) = 3 * localBestNID1 + NID2;
end

fprintf("\n=== PSS/SSS validation ===\n");
fprintf("Checked captures: %d\n", nCheck);
fprintf("Best SSB column start mode: %d\n", mode(bestColStart));
fprintf("Best SSB column start counts:\n");
tabulate(bestColStart);

fprintf("\nPSS corr | median=%.3f | p05=%.3f | p95=%.3f | min=%.3f | max=%.3f\n", ...
    median(bestPSSCorr, "omitnan"), ...
    prctile(bestPSSCorr, 5), ...
    prctile(bestPSSCorr, 95), ...
    min(bestPSSCorr), ...
    max(bestPSSCorr));

fprintf("SSS corr | median=%.3f | p05=%.3f | p95=%.3f | min=%.3f | max=%.3f\n", ...
    median(bestSSSCorr, "omitnan"), ...
    prctile(bestSSSCorr, 5), ...
    prctile(bestSSSCorr, 95), ...
    min(bestSSSCorr), ...
    max(bestSSSCorr));

fprintf("\nDetected NID1 mode: %d\n", mode(NID1Log));
fprintf("Detected NCellID mode: %d\n", mode(NCellIDLog));

%% Optional PBCH/BCH decode validation
enablePBCHDecodeCheck = true;
nPBCHCheck = min(50, nCheck);

if enablePBCHDecodeCheck
    fprintf("\n=== PBCH/BCH decode validation ===\n");

    crcOK = false(nPBCHCheck, 1);
    ibarLog = nan(nPBCHCheck, 1);

    for m = 1:nPBCHCheck
        idx = checkIdx(m);
        colStart = bestColStart(m);

        rxGridSSB = double(dataSSB(ssbRows, colStart:(colStart + 3), idx));

        ncellid = NCellIDLog(m);

        try
            dmrsIndices = nrPBCHDMRSIndices(ncellid);

            dmrsEst = zeros(1, 8);

            for ibar_SSB = 0:7
                refGrid2 = zeros([cfg.NRBSSB * 12, 4]);
                refGrid2(dmrsIndices) = nrPBCHDMRS(ncellid, ibar_SSB);

                [hTmp, nTmp] = nrChannelEstimate(rxGridSSB, refGrid2, "AveragingWindow", [0 1]);

                dmrsEst(ibar_SSB + 1) = 10 * log10(mean(abs(hTmp(:)).^2) / (nTmp + eps));
            end

            ibar_SSB = find(dmrsEst == max(dmrsEst), 1, "first") - 1;
            ibarLog(m) = ibar_SSB;

            if cfg.CenterFrequency <= 3e9
                L_max = 4;
                v = mod(ibar_SSB, L_max);
            else
                L_max = 8;
                v = ibar_SSB;
            end

            refGrid3 = zeros([cfg.NRBSSB * 12, 4]);
            refGrid3(dmrsIndices) = nrPBCHDMRS(ncellid, ibar_SSB);
            refGrid3(sssIndices) = nrSSS(ncellid);

            [hest, nest] = nrChannelEstimate(rxGridSSB, refGrid3, "AveragingWindow", [0 1]);

            [pbchIndices, pbchIndicesInfo] = nrPBCHIndices(ncellid);

            pbchRx = nrExtractResources(pbchIndices, rxGridSSB);
            pbchHest = nrExtractResources(pbchIndices, hest);

            [pbchEq, csi] = nrEqualizeMMSE(pbchRx, pbchHest, nest);

            Qm = pbchIndicesInfo.G / pbchIndicesInfo.Gd;
            csi = repmat(csi.', Qm, 1);
            csi = reshape(csi, [], 1);

            pbchBits = nrPBCHDecode(pbchEq, ncellid, v, nest);
            pbchBits = pbchBits .* csi;

            polarListLength = 8;
            [~, crcBCH] = nrBCHDecode(pbchBits, polarListLength, L_max, ncellid);

            crcOK(m) = (crcBCH == 0);

            fprintf("Capture %4d | colStart=%d | NCellID=%d | ibar=%d | CRC=%d\n", ...
                idx, colStart, ncellid, ibar_SSB, crcBCH);

        catch ME
            fprintf("Capture %4d | PBCH check failed: %s\n", idx, ME.message);
        end
    end

    fprintf("\nPBCH/BCH CRC OK: %d/%d = %.2f %%\n", ...
        sum(crcOK), nPBCHCheck, 100 * sum(crcOK) / nPBCHCheck);

    fprintf("Detected ibar_SSB mode: %d\n", mode(ibarLog(~isnan(ibarLog))));
end

%% Timing offset distribution
if exist("timingOffsets", "var") && any(~isnan(timingOffsets))
    figure("Name", "Timing offsets");
    histogram(timingOffsets(validMask));
    xlabel("Timing offset [samples]");
    ylabel("Count");
    title("Dynamic timing offsets");
end
