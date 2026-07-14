#!/usr/bin/env python3

from pathlib import Path

import numpy as np
from scipy.io import savemat

from src.python.wifi_sensing.wifi_legacy_ofdm import (
    DATA_SUBCARRIERS,
    PILOT_SUBCARRIERS,
    convolutional_encode,
    encode_signal_symbol,
    interleave_48,
    make_signal_bits,
    pilot_values,
)

OUTPUT = Path("results/wifi_matlab_rx/python_lsig_debug.mat")
PSDU_LENGTH = 161


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    bits24 = make_signal_bits(PSDU_LENGTH)
    coded48 = convolutional_encode(bits24)
    interleaved48 = interleave_48(coded48)
    signal_symbol = encode_signal_symbol(PSDU_LENGTH)

    pilots = pilot_values(0)

    print("Python L-SIG")
    print("bits24       :", "".join(map(str, bits24)))
    print("coded48      :", "".join(map(str, coded48)))
    print("interleaved48:", "".join(map(str, interleaved48)))
    print("pilots       :", pilots)
    print("symbol length:", len(signal_symbol))

    savemat(
        OUTPUT,
        {
            "psduLength": np.array([[PSDU_LENGTH]], dtype=np.float64),
            "bits24": np.asarray(bits24, dtype=np.uint8).reshape(-1, 1),
            "coded48": np.asarray(coded48, dtype=np.uint8).reshape(-1, 1),
            "interleaved48": np.asarray(
                interleaved48,
                dtype=np.uint8,
            ).reshape(-1, 1),
            "signalSymbol": np.asarray(
                signal_symbol,
                dtype=np.complex64,
            ).reshape(-1, 1),
            "dataSubcarriers": np.asarray(
                DATA_SUBCARRIERS,
                dtype=np.int16,
            ).reshape(-1, 1),
            "pilotSubcarriers": np.asarray(
                PILOT_SUBCARRIERS,
                dtype=np.int16,
            ).reshape(-1, 1),
        },
        do_compression=False,
    )

    print("Saved:", OUTPUT)


if __name__ == "__main__":
    main()
