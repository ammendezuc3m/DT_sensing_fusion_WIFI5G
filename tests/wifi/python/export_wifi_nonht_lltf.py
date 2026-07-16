#!/usr/bin/env python3

from pathlib import Path

import numpy as np

from src.python.wifi_sensing.wifi_legacy_ofdm import make_beacon_waveform


def main() -> None:
    waveform, mpdu = make_beacon_waveform(
        ssid="USRP_CHANNEL11",
        bssid="02:11:22:33:44:55",
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

    if waveform.size < 400:
        raise RuntimeError(
            f"Waveform demasiado corta: {waveform.size}"
        )

    # Estructura legacy:
    # 0:160   -> L-STF
    # 160:192 -> GI de L-LTF
    # 192:256 -> primer símbolo L-LTF
    # 256:320 -> segundo símbolo L-LTF
    # 320:336 -> GI de L-SIG
    # 336:400 -> símbolo L-SIG

    lltf_time_1 = waveform[192:256].copy()
    lltf_time_2 = waveform[256:320].copy()
    lsig_time = waveform[336:400].copy()

    lltf_frequency_1 = np.fft.fft(lltf_time_1).astype(np.complex64)
    lltf_frequency_2 = np.fft.fft(lltf_time_2).astype(np.complex64)
    lsig_frequency = np.fft.fft(lsig_time).astype(np.complex64)

    # Promedio de los dos L-LTF.
    lltf_frequency = (
        0.5 * (lltf_frequency_1 + lltf_frequency_2)
    ).astype(np.complex64)

    output_dir = Path("tests/golden/references")
    output_dir.mkdir(parents=True, exist_ok=True)

    def save_cf32(path: Path, values: np.ndarray) -> None:
        values = np.asarray(values, dtype=np.complex64).reshape(-1)

        interleaved = np.empty(
            2 * values.size,
            dtype="<f4",
        )
        interleaved[0::2] = values.real
        interleaved[1::2] = values.imag
        interleaved.tofile(path)

    save_cf32(
        output_dir / "wifi_nonht_lltf_frequency_cf32.dat",
        lltf_frequency,
    )

    save_cf32(
        output_dir / "wifi_nonht_lsig_frequency_cf32.dat",
        lsig_frequency,
    )

    save_cf32(
        output_dir / "wifi_nonht_clean_packet_cf32.dat",
        waveform,
    )

    np.savez(
        output_dir / "wifi_nonht_clean_packet_reference.npz",
        waveform=waveform,
        mpdu=np.frombuffer(bytes(mpdu), dtype=np.uint8),
        lltf_frequency=lltf_frequency,
        lsig_frequency=lsig_frequency,
    )

    print("Referencia exportada")
    print(f"Waveform samples : {waveform.size}")
    print(f"MPDU bytes       : {len(mpdu)}")
    print("L-LTF bins       : 64")
    print("L-SIG bins       : 64")

    print("\nSubportadoras L-LTF no nulas:")
    for index, value in enumerate(lltf_frequency):
        if abs(value) > 1e-5:
            print(
                f"bin={index:2d} "
                f"value={value.real:+.6f}"
                f"{value.imag:+.6f}j"
            )


if __name__ == "__main__":
    main()
