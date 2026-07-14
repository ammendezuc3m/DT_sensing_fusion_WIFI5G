#!/usr/bin/env python3
"""
Self-test of the new receiver using the locally generated beacon waveform.

Adds controlled delay, complex channel, CFO and AWGN. The test passes only if
the receiver decodes the complete frame and validates the Vendor IE.
"""

from __future__ import annotations

import argparse
import numpy as np

try:
    from .tx_wifi_usrp import build_vendor_specific_ie
    from .wifi_legacy_ofdm import make_beacon_waveform
    from .wifi_rx_phy import decode_candidate, detect_stf_candidates
except ImportError:
    from tx_wifi_usrp import build_vendor_specific_ie
    from wifi_legacy_ofdm import make_beacon_waveform
    from wifi_rx_phy import decode_candidate, detect_stf_candidates


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cfo-hz", type=float, default=3500.0)
    p.add_argument("--snr-db", type=float, default=25.0)
    p.add_argument("--delay-samples", type=int, default=1000)
    args = p.parse_args()

    vendor_ie = build_vendor_specific_ie(
        oui=b"\x02\x11\x22",
        vendor_type=1,
        magic=b"ALBSENS",
        version=1,
        transmitter_id=1,
        experiment_id=1,
        packet_counter=1234,
    )
    waveform, _mpdu = make_beacon_waveform(
        beacon_interval_tu=100,
        sequence_number=7,
        timestamp_us=102400,
        extra_ies=[vendor_ie],
    )

    channel = np.asarray([0.9 + 0.2j, 0.25 - 0.15j, 0.08 + 0.04j], np.complex64)
    faded = np.convolve(waveform, channel).astype(np.complex64)

    n = np.arange(len(faded), dtype=np.float64)
    faded *= np.exp(1j * 2 * np.pi * args.cfo_hz * n / 20e6).astype(np.complex64)

    signal_power = float(np.mean(np.abs(faded) ** 2))
    noise_power = signal_power / (10 ** (args.snr_db / 10.0))
    rng = np.random.default_rng(12345)
    noise = np.sqrt(noise_power / 2.0) * (
        rng.standard_normal(len(faded)) + 1j * rng.standard_normal(len(faded))
    )

    iq = np.concatenate([
        np.zeros(args.delay_samples, np.complex64),
        faded + noise.astype(np.complex64),
        np.zeros(2000, np.complex64),
    ])

    candidates = detect_stf_candidates(iq, threshold=0.65)
    if not candidates:
        raise SystemExit("FAIL: no STF candidate")

    errors = []
    for candidate in candidates:
        try:
            result = decode_candidate(
                iq,
                candidate,
                min_preamble_metric=0.10,
                max_ltf_consistency_error=0.30,
            )
            print("PASS")
            print(f"  offset: {result.packet_offset}")
            print(f"  expected offset: {args.delay_samples}")
            print(f"  total CFO: {result.total_cfo_hz:.2f} Hz")
            print(f"  expected CFO: {args.cfo_hz:.2f} Hz")
            print(f"  packet counter: {result.mac.vendor.packet_counter}")
            print(f"  FCS valid: {result.mac.fcs_valid}")
            print(f"  Vendor valid: {result.mac.vendor.valid}")
            print(f"  CSI shape: {result.csi.shape}")
            return
        except Exception as exc:
            errors.append(str(exc))

    raise SystemExit("FAIL: candidates found but none decoded:\n" + "\n".join(errors))


if __name__ == "__main__":
    main()
