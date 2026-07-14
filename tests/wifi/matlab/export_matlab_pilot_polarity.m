function export_matlab_pilot_polarity
% Extract legacy OFDM pilot polarity from a valid MATLAB waveform.

scriptDir = fileparts(mfilename("fullpath"));
repoRoot = fileparts(fileparts(fileparts(scriptDir)));
outputFile = fullfile(repoRoot, ...
    "results", "wifi_matlab_rx", "matlab_pilot_polarity.txt");

% Generate enough DATA symbols to observe the full 127-symbol sequence.
cfg = wlanNonHTConfig( ...
    ChannelBandwidth="CBW20", ...
    MCS=0, ...
    PSDULength=380);

psdu = zeros(8*cfg.PSDULength,1,"int8");
waveform = wlanWaveformGenerator(psdu,cfg);
ind = wlanFieldIndices(cfg);

% L-SIG plus all DATA symbols.
lsig = waveform(ind.LSIG(1):ind.LSIG(2));
data = waveform(ind.NonHTData(1):ind.NonHTData(2));

allSymbols = [lsig; data];
numSymbols = floor(numel(allSymbols)/80);

polarity = zeros(numSymbols,1);

for s = 1:numSymbols
    symbol = allSymbols((s-1)*80+1:s*80);
    freq = fft(symbol(17:80),64);

    % Base pilot at -21 is +1, so its sign directly gives polarity.
    value = real(freq(mod(-21,64)+1));

    if value >= 0
        polarity(s) = 1;
    else
        polarity(s) = -1;
    end
end

fprintf("Symbols extracted: %d\n",numSymbols);
fprintf("L-SIG polarity: %d\n",polarity(1));
fprintf("First DATA polarity: %d\n",polarity(2));

fprintf("Sequence including L-SIG:\n");
fprintf("%d,",polarity);
fprintf("\n");

% Save one value per line:
% line 1 = L-SIG, line 2 = first DATA symbol.
writematrix(polarity,outputFile,"Delimiter","tab");

fprintf("Saved: %s\n",outputFile);
end
