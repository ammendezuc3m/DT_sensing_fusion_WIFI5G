function outBits =  dsssCCKDemodulate(rxSym,dataRate,prevSym)
%dsssCCKDemodulate Performs DSSS CCK Demodulation
%   OUTBITS = DSSSCCKDEMODULATE(RXSYM,DATARATE,PREVSYM) demodulates the
%   DSSS CCK code words.
%
%   OUTBITS is a vector containing the CCK demodulated bits.
%
%   RXSYM is a vector containing the CCK modulated symbols.
%
%   DATARATE is a character vector, or a string scalar specifying the
%   data rate, must be '5.5Mbps' or '11Mbps'
%
%   PREVSYM a single or double complex scalar representing the previous
%   symbol used in CCK demodulation.

%   Copyright 2024 The MathWorks, Inc.

% Derive the CCK parameters based on data rate
[numBitsPerCW,codeWord] = generateRefCW(dataRate);

numCW = length(rxSym)/8;
outBits = zeros(1,numCW*numBitsPerCW);
for ii = 1:numCW
    CWCount = ii-1;
    despreadSym = rxSym((CWCount*8)+(1:8));
    decBits = zeros(1,numBitsPerCW-2);
    CCKabs = zeros(1,2^(numBitsPerCW-2));

    % Perform correlation with each of the reference codeword
    for run = 1:2^(numBitsPerCW-2)
        CCKabs(run) = sum(despreadSym.*conj(codeWord(run,:)));
    end

    % Find index of the codeword with maximum correlation
    [~,idx] = max(abs(CCKabs));

    % Convert index to bits to obtain bits from d2 to the end of codeword
    decBits(3:numBitsPerCW) = (int2bit(idx-1,numBitsPerCW-2))';

    % Remove initial phase offset from previous symbol
    QPSKsym = CCKabs(idx)*conj(prevSym);
    tempAngle = mod(angle(QPSKsym),2*pi);

    % Add extra pi phase shift for odd numbered symbols
    if mod(CWCount,2) == 1
        tempAngle = mod(tempAngle + pi, 2*pi);
    end

    % Find the quadrant of the angle and assign bits d0 and d1 based on
    % the quadrant
    if tempAngle > pi/4 && tempAngle < 3*pi/4
        decBits(1:2) = [0 1];
    elseif tempAngle > 3*pi/4 && tempAngle < 5*pi/4
        decBits(1:2) = [1 1];
    elseif tempAngle > 5*pi/4 && tempAngle < 7*pi/4
        decBits(1:2) = [1 0];
    else
        decBits(1:2) = [0 0];
    end

    % Append decoded bits to the output vector 
    outBits(CWCount*numBitsPerCW+(1:numBitsPerCW)) = decBits;
    prevSym = CCKabs(idx)/8;
end
end

function [numBitsPerCW,codeWord] = generateRefCW(dataRate)
% GENERATEREFCW Generate reference codewords for correlation
if strcmp(dataRate,'5.5Mbps')
    numBitsPerCW = 4; % bits d0 to d3
else
    numBitsPerCW = 8; % bits d0 to d7
end
numComb = 2^(numBitsPerCW-2); % number of combinations of bits other than d0 and d1
combinations = (int2bit(0:numComb-1,numBitsPerCW))';
bits = reshape(combinations.',[],1);
codeWord = zeros(8,numComb);
for ii = 1:numComb
    cckSymbols = wlan.internal.dsssCCKModulate(bits((ii-1)*numBitsPerCW+(1:numBitsPerCW)), ...
        dataRate, 0);
    codeWord(:,ii) = wlan.internal.dsssCCKSpread(cckSymbols);
end

codeWord = codeWord.';
end