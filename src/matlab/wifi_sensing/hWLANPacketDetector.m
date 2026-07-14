classdef hWLANPacketDetector < handle & comm.internal.ConfigBase
%hWLANPacketDetector OFDM packet detection using the L-STF
%
%   WPD = hWLANPacketDetector(X,CBW) creates an hWLANPacketDetector object,
%   WPD, that sets the Waveform property to X and ChannelBandwidth to CBW.
%
%   WPD = hWLANPacketDetector(...,Name,Value) creates an
%   hWLANPacketDetector object, WPD, with the specified property Name set
%   to the specified Value. You can specify additional name-value pair
%   arguments in any order as (Name1,Value1,...,NameN,ValueN).
%
%   hWLANPacketDetector methods:
%
%   findPacketStart - Returns the offset to the start of a detected
%                     packet in Waveform
%
%   hWLANPacketDetector properties:
%
%   Waveform           - A time-domain signal specified as a Ns-by-Nr
%                        matrix of real or complex float values where Ns
%                        represents the number of time domain samples and
%                        Nr represents the number of receive antennas.
%   ChannelBandwidth   - A text scalar describing the channel bandwidth of
%                        WAVEFORM. The value must be 'CBW5', 'CBW10',
%                        'CBW20', 'CBW40', 'CBW80', 'CBW160', or 'CBW320'.
%   OversamplingFactor - The oversampling factor of WAVEFORM. The value
%                        must be greater than or equal to 1. The default is
%                        1.
%   Threshold          - The threshold which the decision statistic must
%                        meet or exceed to detect a packet in WAVEFORM when
%                        calling FINDPACKETSTART. The value must be a real
%                        scalar that is greater than 0 and less than or
%                        equal to 1. The default is 0.5.
%   SearchOffset       - The index from which FINDPACKETSTART looks for a
%                        packet. The value must be a real scalar integer
%                        that is greater than or equal to 0 and less than
%                        Ns in WAVEFORM. The default is 0.
%
%
%   % Example 1:
%   %  Detect a received 802.11a packet
%      cfgNonHT = wlanNonHTConfig; % Create packet configuration
%
%      % Generate transmit waveform
%      txWaveform = wlanWaveformGenerator([1;0;0;1],cfgNonHT, ...
%                   'WindowTransitionTime',0);
%
%      % Delay the signal by appending zeros at the start
%      rxWaveform = [zeros(20,1);txWaveform];
%
%      wpd = hWLANPacketDetector(rxWaveform,cfgNonHT.ChannelBandwidth);
%      wpd.SearchOffset = 5;
%      wpd.Threshold = 0.99;
%
%      startOffset = findPacketStart(wpd);
%
%      disp("Packet start offset: " + startOffset)

%   Copyright 2024 The MathWorks, Inc.

    properties
        Waveform {mustBeFloat,mustBeFinite} = [];
        ChannelBandwidth {mustBeTextScalar} = 'CBW20';
        OversamplingFactor (1,1) {mustBeNumeric, mustBeFinite, mustBeGreaterThanOrEqual(OversamplingFactor, 1)} = 1;
        Threshold (1,1) {mustBeFloat, mustBeReal, mustBeInRange(Threshold,0,1,'exclude-lower')} = 0.5;
        SearchOffset (1,1) {mustBeNumeric,mustBeInteger,mustBeNonnegative} = 0;
    end

    properties(Access=private)
        UpdateDecisionStatistic = true;
        UpdatePacketOffsets = true;
        FoundOffsets;
        DecisionStatistic;
        DetectedColumnIndicies;
    end

    methods
        function obj = hWLANPacketDetector(wav,cbw,opts)
            arguments
                wav;
                cbw;
                opts.Threshold
                opts.OversamplingFactor
                opts.SearchOffset
            end
            nvpairs = [{'Waveform' wav 'ChannelBandwidth' cbw} namedargs2cell(opts)];
            obj@comm.internal.ConfigBase(nvpairs{:});
        end

        function [startOffset,M] = findPacketStart(obj)
        %findPacketStart Return the offset of a detected packet
        %
        % [STARTOFFSET,M] = findPacketStart(OBJ) returns the index of a
        % detected packet in WAVEFORM. The index returned is the next
        % closest index to SEARCHOFFSET. If no packet is detected an empty
        % value is returned.
        %
        % OBJ is a hWLANPacketDetector object.
        %
        % STARTOFFSET is an integer scalar indicating the location of the
        % start of a detected packet.
        %
        % M is a real vector of size N-by-1, representing the decision
        % statistics based on auto-correlation of WAVEFORM. The
        % length of N depends on index of a successful detection of a
        % packet.

            if isempty(obj.Waveform)
                startOffset = [];
                M = [];
                return;
            end

            cbw = obj.ChannelBandwidth;
            [fftLen,nsc] = wlan.internal.cbw2nfft(cbw);
            osf = obj.OversamplingFactor;
            wlan.internal.validateOFDMOSF(osf, fftLen, 0); % Validate OSF

            Td = 0.8e-6; % Time period of a short training symbol for 20MHz
            symbolLength = Td*osf*nsc*20e6;

            lenLSTF = symbolLength*10; % Length of 10 L-STF symbols
            lenHalfLSTF = lenLSTF/2;   % Length of 5 L-STF symbols

            if obj.UpdateDecisionStatistic
                inpLength = size(obj.Waveform,1);

                % Append zeros to make the input equal to multiple of L-STF/2
                if inpLength<=lenHalfLSTF
                    numPadSamples = lenLSTF - inpLength;
                else
                    numPadSamples = lenHalfLSTF*ceil(inpLength/lenHalfLSTF) - inpLength;
                end

                padSamples = zeros(numPadSamples,size(obj.Waveform,2),'like',obj.Waveform);

                % Process the input waveform in blocks of L-STF length. The processing
                % blocks are offset by half the L-STF length.
                numBlocks = (inpLength + numPadSamples)/lenHalfLSTF;

                searchBuffer = reshape([obj.Waveform;padSamples],lenHalfLSTF,numBlocks,size(obj.Waveform,2));
                searchBuffer = [searchBuffer(:,1:end-1,:);searchBuffer(:,2:end,:)];

                [obj.FoundOffsets,obj.DecisionStatistic,obj.DetectedColumnIndicies] = ...
                    wlan.internal.detectPackets(searchBuffer,symbolLength, ...
                                                lenLSTF,obj.Threshold);

                obj.UpdateDecisionStatistic = false;
                obj.UpdatePacketOffsets = false;
            elseif obj.UpdatePacketOffsets
                % obj.Threshold was updated so update the offsets
                [obj.FoundOffsets,obj.DetectedColumnIndicies] = ...
                    getPacketOffsets(obj.DecisionStatistic,symbolLength, ...
                                     lenLSTF,obj.Threshold);

                obj.UpdatePacketOffsets = false;
            end

            idx = find(obj.FoundOffsets >= obj.SearchOffset,1);
            startOffset = obj.FoundOffsets(idx);

            if isempty(idx)
                endIdx = size(obj.DecisionStatistic,2);
            else
                endIdx = obj.DetectedColumnIndicies(idx);
            end

            M = double([reshape(obj.DecisionStatistic(1:lenHalfLSTF,1:endIdx-1),[],1); ...
                        reshape(obj.DecisionStatistic(:,endIdx),[],1)]);
        end % function findPacketStart

        % Setters
        function set.Waveform(obj,value)
            if ~isequal(obj.Waveform,value)
                obj.Waveform = value;
                obj.UpdateDecisionStatistic = true; %#ok<*MCSUP>
            end
        end

        function set.ChannelBandwidth(obj,value)
            if ~strcmpi(obj.ChannelBandwidth,value)
                obj.ChannelBandwidth = wlan.internal.validateParam('NONHTEHTCHANBW', value, mfilename);
                obj.UpdateDecisionStatistic = true;
            end
        end

        function set.OversamplingFactor(obj,value)
            if ~isequal(obj.OversamplingFactor,value)
                obj.OversamplingFactor = value;
                obj.UpdateDecisionStatistic = true;
            end
        end

        function set.Threshold(obj,value)
            if ~isequal(obj.Threshold,value)
                obj.Threshold = value;
                obj.UpdatePacketOffsets = true;
            end
        end

        function set.SearchOffset(obj,value)
            coder.internal.errorIf(~isempty(obj.Waveform) && value>size(obj.Waveform, 1)-1, 'wlan:shared:InvalidOffsetValue')
            obj.SearchOffset = value;
        end
    end
end

function [packetStarts,colIdxs] = getPacketOffsets(mn,symbolLength,lenLSTF,threshold)
% mn: Decision statistic
% threshold: Decision threshold as specified by user
    N = mn > threshold;
    colDesc = sum(N) >= symbolLength*1.5;
    N(:,~colDesc) = false;
    colIdxs = find(colDesc);

    % Create a matrix of indicies where each column has the value 1:corrLen
    % then extract indices based on N and desc and calculate all possible
    % packet start locations
    corrLen = lenLSTF - (symbolLength*2) + 1;
    idxs = repmat((1:corrLen)',1,size(N,2));
    idxs(~N) = NaN;
    idxs = idxs(:,colDesc);
    packetStarts = min(idxs) + (colIdxs-1)*lenLSTF/2 - 1;

    % Check relative distances between peaks for all detected packets
    if ~isempty(packetStarts)
        packetStarts = arrayfun(@(x)checkRelativeDist(packetStarts(x),idxs(:,x),symbolLength),1:length(packetStarts));
    end

    % Extract non-NaN values
    colIdxs = colIdxs(~isnan(packetStarts));
    packetStarts = packetStarts(~isnan(packetStarts));

end

function pS = checkRelativeDist(pS,idxs,symbolLength)
% Check the relative distance between peaks relative to the first peak. If
% this exceed three times the symbol length then the packet is not
% detected.
    nonan = idxs(~isnan(idxs));
    if any(nonan(2:symbolLength) - nonan(1)>symbolLength*3)
        pS = NaN;
    end

end
