clear;
clc;
close all;

%% Configuración USRP
serialNumber    = "34B73C3";
centerFrequency = 2.462e9;
sampleRate      = 20e6;
masterClockRate = 20e6;

gainDB          = 70;
samplesPerFrame = 65536;

decimationFactor = masterClockRate / sampleRate;

fprintf("========================================\n");
fprintf("USRP WiFi spectrum receiver\n");
fprintf("========================================\n");
fprintf("Serial            : %s\n", serialNumber);
fprintf("Center frequency  : %.3f MHz\n", centerFrequency/1e6);
fprintf("Sample rate       : %.3f Msps\n", sampleRate/1e6);
fprintf("RX gain           : %.1f dB\n", gainDB);
fprintf("Samples/frame     : %d\n", samplesPerFrame);
fprintf("Frame duration    : %.3f ms\n\n", ...
    samplesPerFrame/sampleRate*1e3);

%% Receptor B210
rxRadio = comm.SDRuReceiver( ...
    Platform          = "B210", ...
    SerialNum         = char(serialNumber), ...
    CenterFrequency   = centerFrequency, ...
    MasterClockRate   = masterClockRate, ...
    DecimationFactor  = decimationFactor, ...
    SamplesPerFrame   = samplesPerFrame, ...
    OutputDataType    = "single", ...
    Gain              = gainDB, ...
    ChannelMapping    = 1);

%% Analizador de espectro
spectrumScope = spectrumAnalyzer( ...
    SampleRate             = sampleRate, ...
    SpectrumType           = "Power density", ...
    FrequencySpan          = "Full", ...
    FrequencyOffset        = centerFrequency, ...
    PlotAsTwoSidedSpectrum = true, ...
    YLimits                = [-120 -20], ...
    Title                  = ...
        "Canal WiFi 11 recibido con USRP B210");

fprintf("Recibiendo indefinidamente.\n");
fprintf("Pulsa Ctrl+C para detener.\n\n");

totalSamples = 0;
overflowCount = 0;
frameCounter = 0;
lastReport = tic;

try
    while true
        [iq, validLength, overflow] = rxRadio();

        if overflow
            overflowCount = overflowCount + 1;
        end

        if validLength <= 0
            continue;
        end

        iq = iq(1:validLength);
        frameCounter = frameCounter + 1;
        totalSamples = totalSamples + validLength;

        % Eliminación de componente DC para visualizar mejor
        iq = iq - mean(iq);

        spectrumScope(iq);

        if toc(lastReport) >= 1
            rmsValue = sqrt(mean(abs(double(iq)).^2));
            peakValue = max(abs(double(iq)));

            fprintf( ...
                "frames=%7d samples=%12d overflows=%d " + ...
                "rms=%.5f peak=%.5f\n", ...
                frameCounter, ...
                totalSamples, ...
                overflowCount, ...
                rmsValue, ...
                peakValue);

            lastReport = tic;
        end
    end

catch exception
    release(rxRadio);
    release(spectrumScope);

    if strcmp(exception.identifier, ...
            "MATLAB:class:InvalidHandle")
        fprintf("Receptor detenido.\n");
    else
        rethrow(exception);
    end
end
