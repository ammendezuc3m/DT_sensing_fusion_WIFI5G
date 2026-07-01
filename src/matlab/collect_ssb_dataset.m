function collect_ssb_dataset(label, movementState, durationSeconds, personId, orientation, pauseBeforeSec)
%COLLECT_SSB_DATASET Captura dataset SSB para una etiqueta concreta.
%
% Uso:
%   collect_ssb_dataset("empty","static",120,"none","none",10)
%   collect_ssb_dataset("P1","static",120,"person01","facing_rx",10)
%   collect_ssb_dataset("P2","static",120,"person01","facing_dot",10)
%
% Guarda:
%   data/raw/session_YYYYMMDD_HHMMSS_LABEL_STATE/
%       session_data.mat
%       metadata.json
%       capture_log.csv

    if nargin < 1 || isempty(label)
        error("Debes indicar label: empty, P1, P2, P3 o P4.");
    end
    if nargin < 2 || isempty(movementState)
        movementState = "static";
    end
    if nargin < 3 || isempty(durationSeconds)
        durationSeconds = 120;
    end
    if nargin < 4 || isempty(personId)
        personId = "person01";
    end
    if nargin < 5 || isempty(orientation)
        orientation = "unknown";
    end
    if nargin < 6 || isempty(pauseBeforeSec)
        pauseBeforeSec = 10;
    end

    label = string(label);
    movementState = string(movementState);
    personId = string(personId);
    orientation = string(orientation);

    allowedLabels = ["empty", "P1", "P2", "P3", "P4"];
    if ~any(label == allowedLabels)
        error("Label no valida. Usa: empty, P1, P2, P3 o P4.");
    end

    rootDir = fullfile(getenv("HOME"), "AlbertoDir", "DT_sensing_fusion");

    targetPath = fullfile(rootDir, "config", "target_ssb.json");
    if ~isfile(targetPath)
        error("No existe config/target_ssb.json. Ejecuta primero calibrate_target_ssb(60).");
    end

    targetConfig = jsondecode(fileread(targetPath));
    targetSSB = targetConfig.target_ssb;

    mapPath = fullfile(rootDir, "config", "map_zones.json");
    mapConfig = jsondecode(fileread(mapPath));

    sessionTimestamp = string(datetime("now", "Format", "yyyyMMdd_HHmmss"));
    sessionId = "session_" + sessionTimestamp + "_" + label + "_" + movementState;

    outDir = fullfile(rootDir, "data", "raw", sessionId);
    mkdir(outDir);

    [rx, params, precomp] = ssb_init_receiver(rootDir);

    % -------------------------
    % Posicion asociada a etiqueta
    % -------------------------
    position = [];
    if label ~= "empty"
        position = mapConfig.x5g_target_zones.(label).position;
    end

    fprintf("\n=== SSB DATASET CAPTURE ===\n");
    fprintf("Session ID: %s\n", sessionId);
    fprintf("Label: %s\n", label);
    fprintf("Movement: %s\n", movementState);
    fprintf("Duration: %.1f s\n", durationSeconds);
    fprintf("Person ID: %s\n", personId);
    fprintf("Orientation: %s\n", orientation);
    fprintf("Target SSB: %d\n", targetSSB);
    fprintf("Output dir: %s\n", outDir);

    if label == "empty"
        fprintf("\nEscena VACIA. Nadie dentro del mapa.\n");
    else
        fprintf("\nColocate en %s antes de que empiece la captura.\n", label);
    end

    fprintf("Empieza en %.0f segundos...\n", pauseBeforeSec);
    for k = pauseBeforeSec:-1:1
        fprintf("%d...\n", k);
        pause(1);
    end

    % Estimacion superior de capturas. Si se queda corto, se amplia dinamicamente.
    maxCaptures = max(100, ceil(durationSeconds * 15));

    hSSB = complex(zeros(240, 4, maxCaptures, "single"));
    rxGridSSB = complex(zeros(240, 4, maxCaptures, "single"));
    gridUseful = complex(zeros(240, 6, maxCaptures, "single"));
    gridFull = complex(zeros(360, 6, maxCaptures, "single"));

    timestampLog = strings(maxCaptures,1);
    ibarLog = nan(maxCaptures,1);
    freqLog = nan(maxCaptures,1);
    noiseLog = nan(maxCaptures,1);
    timingLog = nan(maxCaptures,1);
    ncellidLog = nan(maxCaptures,1);
    nid1Log = nan(maxCaptures,1);
    nid2Log = nan(maxCaptures,1);
    dmrsLog = nan(maxCaptures,8);
    acceptedLog = false(maxCaptures,1);
    reasonLog = strings(maxCaptures,1);

    nAccepted = 0;
    nProcessed = 0;

    tStart = tic;

    while toc(tStart) < durationSeconds
        nProcessed = nProcessed + 1;

        waveform = capture(rx, params.captureDuration);
        out = ssb_process_capture(waveform, rx, params, precomp);

        if out.valid
            useCapture = (out.ibar_SSB == targetSSB);

            if useCapture
                nAccepted = nAccepted + 1;

                if nAccepted > size(hSSB,3)
                    % Ampliar memoria si hiciera falta
                    hSSB(:,:,end+maxCaptures) = complex(single(0), single(0));
                    rxGridSSB(:,:,end+maxCaptures) = complex(single(0), single(0));
                    gridUseful(:,:,end+maxCaptures) = complex(single(0), single(0));
                    gridFull(:,:,end+maxCaptures) = complex(single(0), single(0));

                    timestampLog(end+maxCaptures) = "";
                    ibarLog(end+maxCaptures) = nan;
                    freqLog(end+maxCaptures) = nan;
                    noiseLog(end+maxCaptures) = nan;
                    timingLog(end+maxCaptures) = nan;
                    ncellidLog(end+maxCaptures) = nan;
                    nid1Log(end+maxCaptures) = nan;
                    nid2Log(end+maxCaptures) = nan;
                    dmrsLog(end+maxCaptures,:) = nan;
                    acceptedLog(end+maxCaptures) = false;
                    reasonLog(end+maxCaptures) = "";
                end

                hSSB(:,:,nAccepted) = out.hSSB;
                rxGridSSB(:,:,nAccepted) = out.rxGridSSB;
                gridUseful(:,:,nAccepted) = out.gridUseful;
                gridFull(:,:,nAccepted) = out.gridFull;

                timestampLog(nAccepted) = string(out.timestamp);
                ibarLog(nAccepted) = out.ibar_SSB;
                freqLog(nAccepted) = out.freqOffset;
                noiseLog(nAccepted) = out.noiseVar;
                timingLog(nAccepted) = out.timingOffset;
                ncellidLog(nAccepted) = out.ncellid;
                nid1Log(nAccepted) = out.NID1;
                nid2Log(nAccepted) = out.NID2;
                dmrsLog(nAccepted,:) = out.dmrsEst;
                acceptedLog(nAccepted) = true;
                reasonLog(nAccepted) = "accepted";

                fprintf("OK %04d/%04d | label=%s | ibar=%d | freq=%.2f | noise=%.4g\n", ...
                    nAccepted, nProcessed, label, out.ibar_SSB, out.freqOffset, out.noiseVar);
            else
                fprintf("DROP %04d | ibar=%d != targetSSB=%d\n", ...
                    nProcessed, out.ibar_SSB, targetSSB);
            end
        else
            fprintf("FAIL %04d | %s\n", nProcessed, out.error);
        end
    end

    elapsed = toc(tStart);
    release(rx);

    % Recortar a aceptadas
    hSSB = hSSB(:,:,1:nAccepted);
    rxGridSSB = rxGridSSB(:,:,1:nAccepted);
    gridUseful = gridUseful(:,:,1:nAccepted);
    gridFull = gridFull(:,:,1:nAccepted);

    timestampLog = timestampLog(1:nAccepted);
    ibarLog = ibarLog(1:nAccepted);
    freqLog = freqLog(1:nAccepted);
    noiseLog = noiseLog(1:nAccepted);
    timingLog = timingLog(1:nAccepted);
    ncellidLog = ncellidLog(1:nAccepted);
    nid1Log = nid1Log(1:nAccepted);
    nid2Log = nid2Log(1:nAccepted);
    dmrsLog = dmrsLog(1:nAccepted,:);
    acceptedLog = acceptedLog(1:nAccepted);
    reasonLog = reasonLog(1:nAccepted);

    % -------------------------
    % Guardar .mat
    % -------------------------
    sessionInfo = struct();
    sessionInfo.session_id = char(sessionId);
    sessionInfo.label = char(label);
    sessionInfo.movement_state = char(movementState);
    sessionInfo.person_id = char(personId);
    sessionInfo.orientation = char(orientation);
    sessionInfo.duration_s_requested = durationSeconds;
    sessionInfo.duration_s_elapsed = elapsed;
    sessionInfo.target_ssb = targetSSB;
    sessionInfo.n_processed = nProcessed;
    sessionInfo.n_accepted = nAccepted;
    sessionInfo.accepted_rate_hz = nAccepted / elapsed;
    sessionInfo.processed_rate_hz = nProcessed / elapsed;
    sessionInfo.created_at = char(datetime("now", "TimeZone", "local", "Format", "yyyy-MM-dd'T'HH:mm:ss.SSS"));
    sessionInfo.root_dir = char(rootDir);
    sessionInfo.out_dir = char(outDir);
    sessionInfo.band = char(params.band);
    sessionInfo.gscn = params.GSCN;
    sessionInfo.center_frequency_hz = params.centerFrequency;
    sessionInfo.sample_rate_hz = params.sampleRate;
    sessionInfo.scs_khz = params.scsNumeric;
    sessionInfo.nrb_ssb = params.nrbSSB;
    sessionInfo.demod_rb_debug = params.demodRB;
    sessionInfo.rx_gain = rx.Gain;

    save(fullfile(outDir, "session_data.mat"), ...
        "hSSB", "rxGridSSB", "gridUseful", "gridFull", ...
        "timestampLog", "ibarLog", "freqLog", "noiseLog", ...
        "timingLog", "ncellidLog", "nid1Log", "nid2Log", ...
        "dmrsLog", "acceptedLog", "reasonLog", ...
        "sessionInfo", "params", "-v7.3");

    % -------------------------
    % Metadata JSON
    % -------------------------
    metadata = sessionInfo;
    metadata.map_id = mapConfig.map_id;
    metadata.position = position;
    metadata.window_size_future_model = 5;
    metadata.features_future_model = { ...
        "complex_channel_H", ...
        "amplitude_delta_vs_empty", ...
        "temporal_phase_rotation", ...
        "frequency_differential_phase", ...
        "temporal_amplitude_variance", ...
        "quality_features" ...
    };
    metadata.notes = "Dataset raw. Cada muestra aceptada corresponde al mismo targetSSB. Las ventanas de 5 capturas se generaran despues.";

    jsonText = jsonencode(metadata, PrettyPrint=true);
    fid = fopen(fullfile(outDir, "metadata.json"), "w");
    fwrite(fid, jsonText, "char");
    fclose(fid);

    % -------------------------
    % CSV log
    % -------------------------
    T = table( ...
        timestampLog, ibarLog, freqLog, noiseLog, timingLog, ...
        ncellidLog, nid1Log, nid2Log, acceptedLog, reasonLog, ...
        'VariableNames', { ...
            'timestamp', 'ibar_ssb', 'freq_offset_hz', 'noise_var', 'timing_offset', ...
            'ncellid', 'nid1', 'nid2', 'accepted', 'reason' ...
        });

    writetable(T, fullfile(outDir, "capture_log.csv"));

    fprintf("\n=== SESSION SUMMARY ===\n");
    fprintf("Session: %s\n", sessionId);
    fprintf("Processed captures: %d\n", nProcessed);
    fprintf("Accepted captures: %d\n", nAccepted);
    fprintf("Elapsed: %.2f s\n", elapsed);
    fprintf("Accepted rate: %.2f Hz\n", nAccepted / elapsed);
    fprintf("Saved in: %s\n", outDir);

    if nAccepted < 20
        warning("Muy pocas capturas aceptadas. Revisa targetSSB, orientacion antenas o calidad de recepcion.");
    end
end
