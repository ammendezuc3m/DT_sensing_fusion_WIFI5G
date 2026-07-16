function detect_own_beacons_matched
% Detect the exact Python beacon in the latest RF capture.
%
% Uses a waveform-specific matched filter and a CFO hypothesis bank.

repoRoot = pwd;

captureFile = fullfile( ...
    repoRoot, ...
    "results", ...
    "wifi_matlab_rx", ...
    "raw_wifi_capture.mat");

referenceFile = fullfile( ...
    repoRoot, ...
    "results", ...
    "wifi_matlab_rx", ...
    "first_tx_packet.mat");

if ~isfile(captureFile)
    error("Capture not found: %s",captureFile);
end

if ~isfile(referenceFile)
    error("Reference not found: %s",referenceFile);
end

C = load(captureFile);
R = load(referenceFile);

x = double(C.capturedData(:));
ref = double(R.txPacket(:));
fs = double(C.sampleRate);

fprintf("Capture samples: %d\n",numel(x));
fprintf("Capture duration: %.6f s\n",numel(x)/fs);
fprintf("Reference samples: %d\n",numel(ref));
fprintf("Sample rate: %.3f MHz\n",fs/1e6);

% Include preamble, L-SIG and enough DATA to distinguish this beacon from
% environmental legacy WiFi packets.
templateLength = min(1200,numel(ref));
baseTemplate = ref(1:templateLength);
baseTemplate = baseTemplate - mean(baseTemplate);

% Expected B210-to-B210 offset was previously approximately +9 kHz.
cfoHypotheses = -20000:2000:20000;

nCapture = numel(x);
nTemplate = numel(baseTemplate);
nValid = nCapture-nTemplate+1;

nfft = 2^nextpow2(nCapture+nTemplate-1);
captureFFT = fft(x,nfft);

% Sliding capture energy for normalized correlation.
windowEnergy = filter( ...
    ones(nTemplate,1), ...
    1, ...
    abs(x).^2);

windowEnergy = windowEnergy(nTemplate:end);

bestMetric = zeros(nValid,1);
bestCFO = zeros(nValid,1);

n = (0:nTemplate-1).';

fprintf("Testing %d CFO hypotheses...\n",numel(cfoHypotheses));

for cfo = cfoHypotheses
    template = baseTemplate .* exp(1j*2*pi*cfo*n/fs);
    templateEnergy = sum(abs(template).^2);

    correlationFull = ifft( ...
        captureFFT .* ...
        fft(conj(flipud(template)),nfft));

    correlationValid = correlationFull( ...
        nTemplate:nCapture);

    metric = abs(correlationValid).^2 ./ ...
        (templateEnergy*windowEnergy + eps);

    improved = metric > bestMetric;
    bestMetric(improved) = metric(improved);
    bestCFO(improved) = cfo;
end

% One beacon every 102.4 ms. Use 70 ms minimum separation so that only one
% strong detection is kept for each expected transmission.
minimumSpacing = round(0.070*fs);

maximumMetric = max(bestMetric);

fprintf("Maximum normalized metric: %.6f\n",maximumMetric);

if maximumMetric <= 0
    fprintf("No matched-filter response found.\n");
    return;
end

threshold = max(0.05,0.25*maximumMetric);

[peakValues,peakLocations] = findpeaks( ...
    bestMetric, ...
    "MinPeakHeight",threshold, ...
    "MinPeakDistance",minimumSpacing);

[peakLocations,order] = sort(peakLocations);
peakValues = peakValues(order);

fprintf("Detection threshold: %.6f\n",threshold);
fprintf("Candidate own beacons: %d\n\n",numel(peakLocations));

for k = 1:numel(peakLocations)
    location = peakLocations(k);

    fprintf( ...
        "%2d: offset=%9d time=%9.6f s metric=%.6f CFO=%+.0f Hz\n", ...
        k, ...
        location, ...
        (location-1)/fs, ...
        peakValues(k), ...
        bestCFO(location));
end

if numel(peakLocations) >= 2
    deltas = diff(peakLocations);

    fprintf("\nCandidate spacings:\n");

    for k = 1:numel(deltas)
        fprintf( ...
            "%2d -> %2d: %9d samples = %9.6f ms\n", ...
            k, ...
            k+1, ...
            deltas(k), ...
            1e3*deltas(k)/fs);
    end

    fprintf( ...
        "\nMedian spacing: %.6f ms\n", ...
        1e3*median(deltas)/fs);
end

outputFile = fullfile( ...
    repoRoot, ...
    "results", ...
    "wifi_matlab_rx", ...
    "own_beacon_peaks.mat");

save( ...
    outputFile, ...
    "peakLocations", ...
    "peakValues", ...
    "bestCFO", ...
    "bestMetric", ...
    "threshold");

fprintf("\nSaved: %s\n",outputFile);
end
