#!/usr/bin/env python3
"""
Offline sanity check for the generated WiFi beacon waveform.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

try:
    from .wifi_legacy_ofdm import SAMPLE_RATE, make_beacon_waveform
    from .wifi_csi import extract_all_csi
except ImportError:
    from wifi_legacy_ofdm import SAMPLE_RATE, make_beacon_waveform
    from wifi_csi import extract_all_csi


def add_cfo(x: np.ndarray, cfo_hz: float, sample_rate: float = SAMPLE_RATE) -> np.ndarray:
    n = np.arange(len(x), dtype=np.float64)

    return (x * np.exp(1j * 2 * np.pi * cfo_hz * n / sample_rate)).astype(np.complex64)


def add_awgn(x: np.ndarray, snr_db: float) -> np.ndarray:
    if snr_db <= -999:
        return x

    p = np.mean(np.abs(x) ** 2)
    nvar = p / (10 ** (snr_db / 10))
    noise = np.sqrt(nvar / 2) * (np.random.randn(len(x)) + 1j * np.random.randn(len(x)))

    return (x + noise).astype(np.complex64)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ssid", default="SENSING_WIFI")
    p.add_argument("--bssid", default="02:11:22:33:44:55")
    p.add_argument("--channel", type=int, default=1)
    p.add_argument("--beacon-interval-tu", type=int, default=98)
    p.add_argument("--profile", choices=["minimal_open", "router_like_wpa2"], default="router_like_wpa2")
    p.add_argument("--cfo-hz", type=float, default=0.0)
    p.add_argument("--snr-db", type=float, default=40.0)
    p.add_argument("--output-npz", default="results/wifi_debug/offline_beacon_test.npz")
    args = p.parse_args()

    wf, mpdu = make_beacon_waveform(
        ssid=args.ssid,
        bssid=args.bssid,
        channel=args.channel,
        beacon_interval_tu=args.beacon_interval_tu,
        sequence_number=0,
        timestamp_us=0,
        profile=args.profile,
    )

    pad = np.zeros(5000, dtype=np.complex64)
    rx = np.concatenate([pad, wf, pad]).astype(np.complex64)

    rx = add_cfo(rx, args.cfo_hz)
    rx = add_awgn(rx, args.snr_db)

    results = extract_all_csi(rx, min_metric=0.25)

    out = Path(args.output_npz)
    out.parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        out,
        tx_waveform=wf,
        rx_waveform=rx,
        mpdu=np.frombuffer(mpdu, dtype=np.uint8),
        sample_rate_hz=np.array([SAMPLE_RATE]),
    )

    print(f"Wrote {out}")
    print(f"MPDU length: {len(mpdu)} bytes")
    print(f"Waveform samples: {len(wf)}")
    print(f"Detected packets: {len(results)}")

    if results:
        r = results[0]
        print(f"First offset: {r.offset}")
        print(f"Metric: {r.metric:.3f}")
        print(f"Estimated CFO: {r.cfo_hz:.2f} Hz")
        print(f"CSI shape: {r.csi.shape}")
        print(f"CSI amp mean/std: {np.mean(np.abs(r.csi)):.4f} / {np.std(np.abs(r.csi)):.4f}")


if __name__ == "__main__":
    main()
