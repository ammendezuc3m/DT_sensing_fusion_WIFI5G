#!/usr/bin/env python3

from pathlib import Path

import numpy as np


REFERENCE = Path(
    "tests/golden/references/"
    "wifi_nonht_clean_packet_reference.npz"
)

DATA_SUBCARRIERS = [
    -26, -25, -24, -23, -22,
    -20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10, -9, -8,
    -6, -5, -4, -3, -2, -1,
     1, 2, 3, 4, 5, 6,
     8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
     22, 23, 24, 25, 26,
]


def fft_bin(subcarrier: int) -> int:
    return subcarrier if subcarrier >= 0 else 64 + subcarrier


def main() -> None:
    data = np.load(REFERENCE)

    waveform = np.asarray(
        data["waveform"],
        dtype=np.complex64,
    )

    mpdu = np.asarray(data["mpdu"], dtype=np.uint8)

    data_start = 400
    symbol_samples = 80
    cp_samples = 16

    num_symbols = (
        waveform.size - data_start
    ) // symbol_samples

    constellation = []

    for symbol_index in range(num_symbols):
        start = (
            data_start
            + symbol_index * symbol_samples
            + cp_samples
        )

        time_symbol = waveform[start:start + 64]
        frequency = np.fft.fft(time_symbol)

        constellation.extend(
            frequency[fft_bin(k)]
            for k in DATA_SUBCARRIERS
        )

    constellation = np.asarray(constellation)

    real_levels = np.unique(
        np.round(constellation.real, decimals=4)
    )

    imag_levels = np.unique(
        np.round(constellation.imag, decimals=4)
    )

    print("MPDU bytes:", mpdu.size)
    print("Waveform samples:", waveform.size)
    print("DATA symbols:", num_symbols)

    print("\nNiveles reales no nulos:")
    print(real_levels[np.abs(real_levels) > 1e-4])

    print("\nNiveles imaginarios no nulos:")
    print(imag_levels[np.abs(imag_levels) > 1e-4])

    near_real_axis = np.mean(
        np.abs(constellation.imag) < 1e-4
    )

    print(
        "\nFracción sobre eje real:",
        f"{near_real_axis:.3f}",
    )

    if (
        len(real_levels[np.abs(real_levels) > 1e-4]) <= 2
        and near_real_axis > 0.9
    ):
        print("\nDiagnóstico: DATA BPSK")
    else:
        print("\nDiagnóstico: DATA no parece BPSK pura")


if __name__ == "__main__":
    main()
