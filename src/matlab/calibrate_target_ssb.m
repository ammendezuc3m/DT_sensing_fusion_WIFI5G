function calibrate_target_ssb(durationSeconds)
%CALIBRATE_TARGET_SSB Calibra el SSB index mas estable en escena vacia.
%
% Uso:
%   calibrate_target_ssb(60)

    if nargin < 1 || isempty(durationSeconds)
        durationSeconds = 60;
    end

    rootDir = fullfile(getenv("HOME"), "AlbertoDir", "DT_sensing_fusion");

    [rx, params, precomp] = ssb_init_receiver(rootDir);

    calibTimestamp = string(datetime("now", "Format", "yyyyMMdd_HHmmss"));
    outDir = fullfile(rootDir, "data", "baselines", "target_ssb_calib_" + calibTimestamp);
    mkdir(outDir);

    fprintf("\n=== TARGET SSB CALIBRATION ===\n");
    fprintf("Duracion: %.1f s\n", durationSeconds);
    fprintf("Escena: VACIA\n");
    fprintf("No debe haber nadie dentro del mapa durante esta calibracion.\n");
    fprintf("Empieza en 10 segundos...\n");

    for k = 10:-1:1
        fprintf("%d...\n", k);
        pause(1);
    end

    maxCaptures = ceil(durationSeconds * 15);
    ibarLog = nan(maxCaptures,1);
    freqLog = nan(maxCaptures,1);
    noiseLog = nan(maxCaptures,1);
    timingLog = nan(maxCaptures,1);
    dmrsLog = nan(maxCaptures,8);
    validLog = false(maxCaptures,1);
    errLog = strings(maxCaptures,1);
    timeLog = strings(maxCaptures,1);

    tStart = tic;
    idx = 0;

    while toc(tStart) < durationSeconds
        idx = idx + 1;

        waveform = capture(rx, params.captureDuration);
        out = ssb_process_capture(waveform, rx, params, precomp);

        if out.valid
            validLog(idx) = true;
            ibarLog(idx) = out.ibar_SSB;
            freqLog(idx) = out.freqOffset;
            noiseLog(idx) = out.noiseVar;
            timingLog(idx) = out.timingOffset;
            dmrsLog(idx,:) = out.dmrsEst;
            timeLog(idx) = string(out.timestamp);

            fprintf("Calib %04d OK | ibar=%d | freqOffset=%.2f | noise=%.4g\n", ...
                idx, out.ibar_SSB, out.freqOffset, out.noiseVar);
        else
            errLog(idx) = out.error;
            fprintf("Calib %04d FAIL | %s\n", idx, out.error);
        end
    end

    release(rx);

    % Recortar arrays
    ibarLog = ibarLog(1:idx);
    freqLog = freqLog(1:idx);
    noiseLog = noiseLog(1:idx);
    timingLog = timingLog(1:idx);
    dmrsLog = dmrsLog(1:idx,:);
    validLog = validLog(1:idx);
    errLog = errLog(1:idx);
    timeLog = timeLog(1:idx);

    validIbar = ibarLog(validLog & ~isnan(ibarLog));

    if isempty(validIbar)
        error("No se ha podido calibrar targetSSB: no hay capturas validas.");
    end

    counts = zeros(1,8);
    for ibar = 0:7
        counts(ibar+1) = sum(validIbar == ibar);
    end

    [~, bestIdx] = max(counts);
    targetSSB = bestIdx - 1;

    validRatio = sum(validLog) / numel(validLog);
    targetRatio = sum(validIbar == targetSSB) / numel(validIbar);

    fprintf("\n=== Calibration summary ===\n");
    fprintf("Capturas totales: %d\n", numel(validLog));
    fprintf("Capturas validas: %d\n", sum(validLog));
    fprintf("Ratio valido: %.2f %%\n", 100*validRatio);
    fprintf("Conteo ibar 0..7:\n");
    disp(counts);
    fprintf("targetSSB elegido: %d\n", targetSSB);
    fprintf("Estabilidad targetSSB dentro de validas: %.2f %%\n", 100*targetRatio);

    save(fullfile(outDir, "target_ssb_calibration.mat"), ...
        "targetSSB", "counts", "validRatio", "targetRatio", ...
        "ibarLog", "freqLog", "noiseLog", "timingLog", "dmrsLog", ...
        "validLog", "errLog", "timeLog", "params", "-v7.3");

    targetStruct = struct();
    targetStruct.target_ssb = targetSSB;
    targetStruct.created_at = char(datetime("now", "TimeZone", "local", "Format", "yyyy-MM-dd'T'HH:mm:ss.SSS"));
    targetStruct.duration_s = durationSeconds;
    targetStruct.valid_captures = sum(validLog);
    targetStruct.total_captures = numel(validLog);
    targetStruct.valid_ratio = validRatio;
    targetStruct.target_ratio_among_valid = targetRatio;
    targetStruct.ibar_counts_0_to_7 = counts;
    targetStruct.band = char(params.band);
    targetStruct.gscn = params.GSCN;
    targetStruct.center_frequency_hz = params.centerFrequency;
    targetStruct.scs_khz = params.scsNumeric;
    targetStruct.notes = "Usar este ibar_SSB para filtrar capturas del dataset.";

    jsonText = jsonencode(targetStruct, PrettyPrint=true);

    fid = fopen(fullfile(rootDir, "config", "target_ssb.json"), "w");
    fwrite(fid, jsonText, "char");
    fclose(fid);

    fid = fopen(fullfile(outDir, "target_ssb.json"), "w");
    fwrite(fid, jsonText, "char");
    fclose(fid);

    fprintf("\nGuardado:\n");
    fprintf("  %s\n", fullfile(rootDir, "config", "target_ssb.json"));
    fprintf("  %s\n", fullfile(outDir, "target_ssb_calibration.mat"));
end

