function analyze_tx_bursts
% Analyze burst duration and periodicity in the TX-ON capture.

scriptDir = fileparts(mfilename("fullpath"));
repoRoot = fileparts(fileparts(fileparts(scriptDir)));
captureFile = fullfile(repoRoot, ...
    "results", "wifi_matlab_rx", "tx_on.mat");

fprintf("Capture file: %s\n", captureFile);

if ~isfile(captureFile)
    error("Capture file does not exist: %s", captureFile);
end

S = load(captureFile);

x = double(S.capturedData(:));
fs = double(S.sampleRate);

fprintf("Samples: %d\n", numel(x));
fprintf("Duration: %.6f s\n", numel(x)/fs);

%% Energy envelope: 20 samples = 1 us at 20 Msps
binSamples = 20;
nBins = floor(numel(x)/binSamples);

powerBins = mean(reshape( ...
    abs(x(1:nBins*binSamples)).^2, ...
    binSamples, nBins), 1);

relativeDB = 10*log10((powerBins + eps)/(median(powerBins) + eps));

thresholdDB = 6;
active = find(relativeDB > thresholdDB);

if isempty(active)
    fprintf("No groups detected above %.1f dB.\n", thresholdDB);
    return;
end

groupStarts = active([true, diff(active) > 1]);
groupEnds = active([diff(active) > 1, true]);

numGroups = numel(groupStarts);

startSample = zeros(numGroups,1);
endSample = zeros(numGroups,1);
durationSamples = zeros(numGroups,1);
peakSample = zeros(numGroups,1);
peakDB = zeros(numGroups,1);

for k = 1:numGroups
    startSample(k) = (groupStarts(k)-1)*binSamples;
    endSample(k) = groupEnds(k)*binSamples-1;
    durationSamples(k) = endSample(k)-startSample(k)+1;

    [peakDB(k), relPeak] = ...
        max(relativeDB(groupStarts(k):groupEnds(k)));

    peakBin = groupStarts(k)+relPeak-1;
    peakSample(k) = (peakBin-1)*binSamples;
end

fprintf("\nEnergy groups detected: %d\n", numGroups);

%% Show strongest long groups
valid = find(durationSamples >= 500);
[~, order] = sort(peakDB(valid), "descend");
valid = valid(order);

fprintf("\nStrong groups with duration >= 500 samples:\n");

if isempty(valid)
    fprintf("None.\n");
else
    for n = 1:min(30,numel(valid))
        k = valid(n);

        fprintf("%2d: start=%10d peak=%10d duration=%6d samples = %8.2f us peak=%6.2f dB\n", ...
            n, ...
            startSample(k), ...
            peakSample(k), ...
            durationSamples(k), ...
            durationSamples(k)/fs*1e6, ...
            peakDB(k));
    end
end

%% Periodicity among strong groups
strong = find(peakDB >= 8 & durationSamples >= 200);
strongPeaks = sort(peakSample(strong));

expectedPeriod = round(0.1024*fs);
tolerance = 10000;

fprintf("\nStrong bursts used for periodicity: %d\n", ...
    numel(strongPeaks));
fprintf("Expected period: %d samples = %.6f ms\n", ...
    expectedPeriod, expectedPeriod/fs*1e3);

pairCount = 0;

for i = 1:numel(strongPeaks)-1
    differences = strongPeaks(i+1:end)-strongPeaks(i);
    matching = find(abs(differences-expectedPeriod) <= tolerance);

    for j = matching(:).'
        pairCount = pairCount+1;
        second = i+j;
        delta = strongPeaks(second)-strongPeaks(i);

        fprintf("%2d: %10d -> %10d  delta=%8d samples = %10.6f ms  error=%d\n", ...
            pairCount, ...
            strongPeaks(i), ...
            strongPeaks(second), ...
            delta, ...
            delta/fs*1e3, ...
            delta-expectedPeriod);
    end
end

if pairCount == 0
    fprintf("No burst pairs near 102.4 ms were found.\n");
end

%% Envelope autocorrelation around 102.4 ms
coarseBin = 200;
nCoarse = floor(numel(x)/coarseBin);

coarsePower = mean(reshape( ...
    abs(x(1:nCoarse*coarseBin)).^2, ...
    coarseBin, nCoarse), 1);

coarsePower = coarsePower-median(coarsePower);

expectedLagBins = round(expectedPeriod/coarseBin);
lagRange = max(1,expectedLagBins-150): ...
    min(numel(coarsePower)-1,expectedLagBins+150);

scores = zeros(size(lagRange));

for k = 1:numel(lagRange)
    lag = lagRange(k);

    a = coarsePower(1:end-lag);
    b = coarsePower(1+lag:end);

    scores(k) = abs(sum(a.*b)) / ...
        sqrt(sum(a.^2)*sum(b.^2)+eps);
end

[bestScore,bestIndex] = max(scores);
bestLagSamples = lagRange(bestIndex)*coarseBin;

fprintf("\nEnvelope autocorrelation:\n");
fprintf("Best nearby lag: %d samples = %.6f ms\n", ...
    bestLagSamples, bestLagSamples/fs*1e3);
fprintf("Period error: %d samples\n", ...
    bestLagSamples-expectedPeriod);
fprintf("Normalized metric: %.6f\n", bestScore);
end
