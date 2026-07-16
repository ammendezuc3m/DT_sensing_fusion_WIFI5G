#!/usr/bin/env python3

from pathlib import Path

import numpy as np

from src.python.wifi_sensing.wifi_legacy_ofdm import make_beacon_waveform


SAMPLE_RATE = 20e6

# Legacy preamble:
# L-STF = 160 samples
# L-LTF = 160 samples
LEGACY_PREAMBLE_SAMPLES = 320


def main() -> None:
    waveform, _ = make_beacon_waveform(
        ssid="REFERENCE",
        bssid="02:00:00:00:00:01",
        channel=11,
        beacon_interval_tu=100,
        sequence_number=0,
        timestamp_us=0,
        profile="router_like_wpa2",
        extra_ies=[],
    )

    waveform = np.asarray(
        waveform,
        dtype=np.complex64,
    ).reshape(-1)

    if waveform.size < LEGACY_PREAMBLE_SAMPLES:
        raise RuntimeError(
            f"Waveform demasiado corta: {waveform.size}"
        )

    reference = waveform[:LEGACY_PREAMBLE_SAMPLES].copy()

    peak = float(np.max(np.abs(reference)))
    if peak <= 0:
        raise RuntimeError("Referencia con amplitud cero")

    # Normalización de energía; la amplitud RF no debe afectar
    # a la correlación.
    reference /= np.sqrt(
        np.sum(np.abs(reference) ** 2)
    ).astype(np.float32)

    output = Path(
        "tests/golden/references/"
        "wifi_nonht_preamble_cf32.dat"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    # complex64 little-endian: Re0, Im0, Re1, Im1...
    interleaved = np.empty(
        reference.size * 2,
        dtype="<f4",
    )
    interleaved[0::2] = reference.real
    interleaved[1::2] = reference.imag
    interleaved.tofile(output)

    print(f"Archivo       : {output}")
    print(f"Muestras      : {reference.size}")
    print(f"Duración      : {reference.size / SAMPLE_RATE * 1e6:.3f} us")
    print(f"Energía       : {np.sum(np.abs(reference) ** 2):.6f}")
    print(f"Pico          : {np.max(np.abs(reference)):.6f}")


if __name__ == "__main__":
    main()
