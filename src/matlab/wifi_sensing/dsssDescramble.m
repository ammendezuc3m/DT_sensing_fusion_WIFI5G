function [y, reg] = dsssDescramble(x,scramInit)
%dsssDescramble Performs DSSS descrambling on the binary input
%
%   Y = DSSSDESCRAMBLE(X,SCRAMINIT) Descrambles the binary
%   input X using a frame-synchronous scrambler.
%
%   Y is a binary column vector or a matrix of type int8 or double with the
%   same size and type as the input X.
%
%   X is a binary column vector or a matrix of type int8 or double and is
%   scrambled with a length-127 frame-synchronous scrambler. Each column of
%   X is scrambled independently with the same initial state. The
%   frame-synchronous scrambler uses the generator polynomial defined in
%   IEEE(R) standard 802.11b-1999, Section 18.2.4. The same scrambler
%   structure is used to scramble bits at the transmitter and descramble
%   bits at the receiver.
%
%   SCRAMINIT is the initial state of the scrambler. It is a 1-by-7 row
%   vector of binary bits of type int8 or double.

%   Copyright 2024 The MathWorks, Inc.

dataLen = length(x);
scramPoly = [1 0 0 0 1 0 0 1]; % rightmost is z^-7;
y = zeros(dataLen,1);

reg = scramInit;
for ii = 1:dataLen
    % Select bits and XOR them
    y(ii) = mod(sum([x(ii) reg].*scramPoly),2);

    % Store latest values in the registers
    reg(2:end) = reg(1:end-1);
    reg(1) = x(ii);
end

end