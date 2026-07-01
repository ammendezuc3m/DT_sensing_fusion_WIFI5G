%% Export hSSB estimates for dataSSB block dataset
% Genera ficheros NPZ-compatible via MAT v7.3? No: MATLAB guarda .mat.
% Este script guarda .mat con hSSB y metadatos; el pipeline Python actual
% consume NPZ. Si se quiere usar directamente desde Python, convertir estos
% .mat a NPZ con h5py o guardar desde MATLAB como .mat y adaptar --hssb-dir.
%
% Uso desde la raiz del proyecto:
%   matlab -batch "run('src/matlab/export_datassb_hssb_dataset.m')"

clear;
clc;

scriptDir = fileparts(mfilename("fullpath"));
projectRoot = fileparts(fileparts(scriptDir));

datasetDir = fullfile(projectRoot, "data", "dataset_datassb", "datassb_side_v1_6labels");
outDir = fullfile(projectRoot, "data", "processed_datassb", "datassb_side_v1_6labels_hssb_mat");

labels = ["empty", "P5"];
nCellIDDefault = 0;
ibarSSBDefault = 0;

if ~exist(outDir, "dir")
    mkdir(outDir);
end

fprintf("Dataset: %s\n", datasetDir);
fprintf("Output:  %s\n", outDir);

for label = labels
    files = dir(fullfile(datasetDir, label, "**", "*.mat"));
    fprintf("\nLabel %s | files=%d\n", label, numel(files));

    for f = 1:numel(files)
        matFile = fullfile(files(f).folder, files(f).name);
        relFolder = erase(files(f).folder, datasetDir);
        relFolder = regexprep(relFolder, "^[/\\]+", "");
        targetFolder = fullfile(outDir, relFolder);
        if ~exist(targetFolder, "dir")
            mkdir(targetFolder);
        end

        [~, stem] = fileparts(files(f).name);
        outFile = fullfile(targetFolder, stem + "_hssb.mat");

        fprintf("Loading %s\n", matFile);
        S = load(matFile, "dataSSB", "validMask", "blockIndex", "sessionId", "validation");

        dataSSB = S.dataSSB;
        validMask = logical(S.validMask(:));
        nCaptures = size(dataSSB, 3);
        nValid = min(numel(validMask), nCaptures);
        validMask = validMask(1:nValid);

        ncellid = nCellIDDefault;
        ibarSSB = ibarSSBDefault;
        if isfield(S, "validation")
            if isfield(S.validation, "NCellIDMode") && ~isnan(S.validation.NCellIDMode)
                ncellid = double(S.validation.NCellIDMode);
            end
            if isfield(S.validation, "IbarSSBMode") && ~isnan(S.validation.IbarSSBMode)
                ibarSSB = double(S.validation.IbarSSBMode);
            end
        end

        dmrsIndices = nrPBCHDMRSIndices(ncellid);
        sssIndices = nrSSSIndices;
        refGrid = zeros([240, 4]);
        refGrid(dmrsIndices) = nrPBCHDMRS(ncellid, ibarSSB);
        refGrid(sssIndices) = nrSSS(ncellid);

        hSSB = complex(zeros(240, 4, nCaptures, "single"));
        noiseVar = nan(nCaptures, 1, "single");

        for i = 1:nCaptures
            if i > nValid || ~validMask(i)
                continue;
            end

            rxGridSSB = double(dataSSB(61:300, 2:5, i));
            try
                [hest, nest] = nrChannelEstimate(rxGridSSB, refGrid, "AveragingWindow", [0 1]);
                hSSB(:, :, i) = single(hest(:, :, 1));
                noiseVar(i) = single(nest);
            catch ME
                warning("hSSB failed for %s capture %d: %s", files(f).name, i, ME.message);
            end
        end

        labelName = char(label);
        sourceFile = matFile;
        blockIndex = NaN;
        sessionId = "";
        if isfield(S, "blockIndex")
            blockIndex = S.blockIndex;
        end
        if isfield(S, "sessionId")
            sessionId = S.sessionId;
        end

        save( ...
            outFile, ...
            "hSSB", "noiseVar", "validMask", "labelName", "blockIndex", ...
            "sessionId", "sourceFile", "ncellid", "ibarSSB", ...
            "-v7.3");

        fprintf("Saved %s | valid=%d/%d | NCellID=%d | ibar=%d\n", ...
            outFile, sum(validMask), nCaptures, ncellid, ibarSSB);
    end
end

fprintf("\nDone.\n");
