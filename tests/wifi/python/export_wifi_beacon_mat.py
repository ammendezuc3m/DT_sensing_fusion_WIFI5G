#!/usr/bin/env python3

from pathlib import Path

import numpy as np
from scipy.io import savemat

from src.python.wifi_sensing.tx_wifi_usrp import build_vendor_specific_ie
from src.python.wifi_sensing.wifi_legacy_ofdm import make_beacon_waveform


OUTPUT = Path("results/wifi_matlab_rx/python_beacon_waveform.mat")


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    vendor_ie = build_vendor_specific_ie(
        oui=b"\x02\x11\x22",
        vendor_type=1,
        magic=b"ALBSENS",
        version=1,
        transmitter_id=1,
        experiment_id=1,
        packet_counter=12345,
    )

    waveform, metadata = make_beacon_waveform(
        beacon_interval_tu=100,
        sequence_number=1,
        timestamp_us=102400,
        extra_ies=[vendor_ie],
    )

    waveform = np.asarray(waveform, dtype=np.complex64).reshape(-1, 1)

    # Añadimos silencio delante y detrás para que el detector MATLAB
    # no encuentre el paquete pegado al comienzo del archivo.
    guard_before = np.zeros((4000, 1), dtype=np.complex64)
    guard_after = np.zeros((4000, 1), dtype=np.complex64)

    captured_data = np.concatenate(
        [guard_before, waveform, guard_after],
        axis=0,
    )

    savemat(
        OUTPUT,
        {
            "capturedData": captured_data,
            "waveform": waveform,
            "sampleRate": np.array([[20e6]], dtype=np.float64),
            "centerFrequency": np.array([[2.412e9]], dtype=np.float64),
            "packetCounterExpected": np.array([[12345]], dtype=np.uint32),
        },
        do_compression=False,
    )

    print(f"Waveform samples: {len(waveform)}")
    print(f"CapturedData samples: {len(captured_data)}")
    print(f"Peak amplitude: {np.max(np.abs(waveform)):.6f}")
    print(f"RMS amplitude: {np.sqrt(np.mean(np.abs(waveform)**2)):.6f}")
    print(f"Metadata: {metadata}")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
