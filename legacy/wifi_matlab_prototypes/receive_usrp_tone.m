clear;
clc;
close all;

%% Configuración
serialNumber   = "34B73C3";
centerFrequency = 2462e6;
sampleRate      = 2e6;
gainDB          = 40;
samplesPerFrame = 32768;
durationSeconds = inf;

fprintf("========================================\n");
fprintf("USRP B210 MATLAB tone receiver\n");
fprintf("========================================\n");
fprintf("Serial              : %s\n", serialNumber);
fprintf("Center frequency    : %.6f MHz\n", centerFrequency/1e6);
fprintf("Sample rate         : %.3f Msps\n", sampleRate/1e6);
fprintf("RX gain             : %.1f dB\n", gainDB);
fprintf("Samples per frame   : %d\n", samplesPerFrame);
fprintf("Expected tone       : +500 kHz\n");
fprintf("Expected RF freq.   : %.6f MHz\n\n", ...
    (centerFrequency + 500e3)/1e6);

%% B210: master clock y decimación
%
% SampleRate = MasterClockRate / DecimationFactor
%
% 20 MHz / 10 = 2 Msps
masterClockRate  = 20e6;
decimationFactor = round(masterClockRate/sampleRate);

%% Crear receptor USRP
rx = comm.SDRuReceiver( ...
    Platform          = "B210", ...
    SerialNum         = char(serialNumber), ...
    CenterFrequency   = centerFrequency, ...
    MasterClockRate   = masterClockRate, ...
    DecimationFactor  = decimationFactor, ...
    Gain              = gainDB, ...
    ChannelMapping    = 1, ...
    SamplesPerFrame   = samplesPerFrame, ...
    OutputDataType    = "single");

%% Analizador de espectro
spectrum = spectrumAnalyzer( ...
    SampleRate          = sampleRate, ...
    SpectrumType        = "Power density", ...
    FrequencySpan       = "Full", ...
    FrequencyOffset     = centerFrequency, ...
    PlotAsTwoSidedSpectrum = true, ...
    YLimits             = [-120 -20], ...
    Title               = "USRP B210 received spectrum");

%% Ventana FFT para estimar numéricamente el pico
fftLength = 65536;
windowFFT = hann(samplesPerFrame, "periodic");

frequencyAxis = ...
    (-fftLength/2 : fftLength/2-1).' ...
    * sampleRate/fftLength;

%% Estadísticas
totalSamples = 0;
totalLostSamples = 0;
frameCounter = 0;

startTime = tic;
lastPrint = tic;

fprintf("Receiving indefinitely.\n");
fprintf("Press Ctrl+C to stop.\n\n");

try
    while toc(startTime) < durationSeconds
        [iq, validLength, lostSamples] = rx();

        if validLength <= 0
            continue;
        end

        iq = iq(1:validLength);

        frameCounter = frameCounter + 1;
        totalSamples = totalSamples + validLength;
        totalLostSamples = totalLostSamples + double(lostSamples);

        %% Dibujar espectro
        spectrum(iq);

        %% Buscar el pico evitando DC
        if validLength == samplesPerFrame
            x = double(iq(:)) .* windowFFT;

            X = fftshift(fft(x, fftLength));
            powerDB = 20*log10(abs(X) + eps);

            % Ignorar ±30 kHz alrededor de DC, porque la fuga del LO
            % puede crear un pico artificial en la frecuencia central.
            validBins = abs(frequencyAxis) > 30e3;

            candidatePower = powerDB;
            candidatePower(~validBins) = -Inf;

            [peakDB, peakIndex] = max(candidatePower);
            peakOffsetHz = frequencyAxis(peakIndex);
            peakRFHz = centerFrequency + peakOffsetHz;

            if toc(lastPrint) >= 1
                fprintf( ...
                    "Frame=%6d | samples=%10d | lost=%d | " + ...
                    "peak offset=%+9.1f kHz | RF=%.6f MHz | " + ...
                    "peak=%.1f dB\n", ...
                    frameCounter, ...
                    totalSamples, ...
                    totalLostSamples, ...
                    peakOffsetHz/1e3, ...
                    peakRFHz/1e6, ...
                    peakDB);

                lastPrint = tic;
            end
        end
    end

catch errorInfo
    release(rx);
    release(spectrum);
    rethrow(errorInfo);
end

release(rx);
release(spectrum);

fprintf("\n========================================\n");
fprintf("Reception finished\n");
fprintf("========================================\n");
fprintf("Frames            : %d\n", frameCounter);
fprintf("Received samples  : %d\n", totalSamples);
fprintf("Lost samples      : %d\n", totalLostSamples);
