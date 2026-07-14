function compare_python_matlab_lsig
% Compare every L-SIG processing stage against MATLAB WLAN Toolbox.

scriptDir = fileparts(mfilename("fullpath"));
repoRoot = fileparts(fileparts(fileparts(scriptDir)));

inputFile = fullfile( ...
    repoRoot, ...
    "results", ...
    "wifi_matlab_rx", ...
    "python_lsig_debug.mat");

S = load(inputFile);

pythonBits24 = double(S.bits24(:));
pythonCoded48 = double(S.coded48(:));
pythonInterleaved48 = double(S.interleaved48(:));
pythonSymbol = double(S.signalSymbol(:));

cfg = wlanNonHTConfig( ...
    ChannelBandwidth="CBW20", ...
    MCS=0, ...
    PSDULength=double(S.psduLength));

% Generate a standards-compliant MATLAB packet.
psdu = zeros(8*double(S.psduLength),1,"int8");
referenceWaveform = wlanWaveformGenerator(psdu,cfg);

ind = wlanFieldIndices(cfg);
matlabSymbol = double(referenceWaveform(ind.LSIG(1):ind.LSIG(2)));

% L-SIG waveform comparison.
rawCorrelation = normalizedCorrelation( ...
    pythonSymbol, ...
    matlabSymbol);

fprintf("Time-domain L-SIG correlation: %.12f\n", ...
    rawCorrelation);

% Remove CP and transform both symbols to frequency domain.
pythonFreq = fft(pythonSymbol(17:80),64);
matlabFreq = fft(matlabSymbol(17:80),64);

dataSC = double(S.dataSubcarriers(:));
pilotSC = double(S.pilotSubcarriers(:));

pythonInterleavedFromFFT = zeros(48,1);
matlabInterleavedFromFFT = zeros(48,1);

for k = 1:48
    bin = mod(dataSC(k),64)+1;

    pythonInterleavedFromFFT(k) = ...
        real(pythonFreq(bin)) < 0;

    matlabInterleavedFromFFT(k) = ...
        real(matlabFreq(bin)) < 0;
end

fprintf("\nInterleaved bits extracted from FFT\n");
printBits("Python exported", pythonInterleaved48);
printBits("Python FFT     ", pythonInterleavedFromFFT);
printBits("MATLAB FFT     ", matlabInterleavedFromFFT);

fprintf("Python export vs Python FFT mismatches: %d / 48\n", ...
    nnz(pythonInterleaved48 ~= pythonInterleavedFromFFT));

fprintf("Python vs MATLAB interleaved mismatches: %d / 48\n", ...
    nnz(pythonInterleavedFromFFT ~= matlabInterleavedFromFFT));

fprintf("\nMismatching interleaved positions, 1-based:\n");
disp(find( ...
    pythonInterleavedFromFFT ~= matlabInterleavedFromFFT ...
).');

% Undo the BPSK interleaver.
pythonCodedFromFFT = deinterleave48( ...
    pythonInterleavedFromFFT);

matlabCodedFromFFT = deinterleave48( ...
    matlabInterleavedFromFFT);

fprintf("\nConvolutionally coded bits\n");
printBits("Python exported", pythonCoded48);
printBits("Python FFT     ", pythonCodedFromFFT);
printBits("MATLAB FFT     ", matlabCodedFromFFT);

fprintf("Python vs MATLAB coded mismatches: %d / 48\n", ...
    nnz(pythonCodedFromFFT ~= matlabCodedFromFFT));

fprintf("\nMismatching coded positions, 1-based:\n");
disp(find(pythonCodedFromFFT ~= matlabCodedFromFFT).');

% Test whether MATLAB differs only by swapping each encoder output pair.
pythonPairSwapped = reshape( ...
    flipud(reshape(pythonCodedFromFFT,2,[])), ...
    [],1);

fprintf("\nPair-order test\n");
fprintf("Normal coded mismatches     : %d / 48\n", ...
    nnz(pythonCodedFromFFT ~= matlabCodedFromFFT));

fprintf("Pair-swapped mismatches     : %d / 48\n", ...
    nnz(pythonPairSwapped ~= matlabCodedFromFFT));

% Pilot comparison.
fprintf("\nPilots\n");

for k = 1:numel(pilotSC)
    bin = mod(pilotSC(k),64)+1;

    fprintf( ...
        "SC %+3d: Python=%+.3f%+.3fj MATLAB=%+.3f%+.3fj\n", ...
        pilotSC(k), ...
        real(pythonFreq(bin)), imag(pythonFreq(bin)), ...
        real(matlabFreq(bin)), imag(matlabFreq(bin)));
end

% Recover the reference MATLAB L-SIG bits normally.
guardedReference = [
    complex(zeros(4000,1));
    referenceWaveform;
    complex(zeros(4000,1))
];

[status,res] = recoverPreamble( ...
    guardedReference, ...
    "CBW20", ...
    0);

syncData = guardedReference(res.PacketOffset+1:end) ...
    ./sqrt(res.LSTFPower);

syncData = frequencyOffset( ...
    syncData, ...
    20e6, ...
    -res.CFOEstimate);

probe = wlanNonHTConfig(ChannelBandwidth="CBW20");
probeInd = wlanFieldIndices(probe);

[matlabBits24,failCheck] = wlanLSIGRecover( ...
    syncData(probeInd.LSIG(1):probeInd.LSIG(2)), ...
    res.ChanEstNonHT, ...
    res.NoiseEstNonHT, ...
    "CBW20");

fprintf("\nUncoded 24-bit L-SIG\n");
printBits("Python", pythonBits24);
printBits("MATLAB", double(matlabBits24(:)));

fprintf("Uncoded mismatches: %d / 24\n", ...
    nnz(pythonBits24 ~= double(matlabBits24(:))));
fprintf("MATLAB failCheck: %d\n",failCheck);
fprintf("Preamble status: %s\n",string(status));
end


function coded = deinterleave48(interleaved)
coded = zeros(48,1);

for k0 = 0:47
    i0 = 3*mod(k0,16)+floor(k0/16);
    coded(k0+1) = interleaved(i0+1);
end
end


function printBits(label,bits)
fprintf("%-16s: ",label);
fprintf("%d",bits);
fprintf("\n");
end


function value = normalizedCorrelation(a,b)
a = a(:);
b = b(:);

value = abs(a'*b)^2 / ...
    ((a'*a)*(b'*b)+eps);

value = real(value);
end
