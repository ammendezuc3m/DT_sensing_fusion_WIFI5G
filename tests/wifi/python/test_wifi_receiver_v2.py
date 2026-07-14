#!/usr/bin/env python3
from __future__ import annotations

import numpy as np

from src.python.wifi_sensing.tx_wifi_usrp import build_vendor_specific_ie
from src.python.wifi_sensing.wifi_legacy_ofdm import make_beacon_waveform
from src.python.wifi_sensing.wlan_rx import ReceiverConfig, decode_capture


def main() -> None:
    vendor_ie = build_vendor_specific_ie(
        oui=b"\x02\x11\x22",
        vendor_type=1,
        magic=b"ALBSENS",
        version=1,
        transmitter_id=1,
        experiment_id=1,
        packet_counter=4321,
    )
    waveform, _ = make_beacon_waveform(
        beacon_interval_tu=100,
        sequence_number=11,
        timestamp_us=102400,
        extra_ies=[vendor_ie],
    )

    rng = np.random.default_rng(1234)
    channel = np.asarray([0.95 + 0.05j, 0.15 - 0.08j, 0.04 + 0.02j], np.complex64)
    faded = np.convolve(waveform, channel).astype(np.complex64)
    cfo_hz = 7000.0
    n = np.arange(len(faded))
    faded *= np.exp(1j * 2*np.pi*cfo_hz*n/20e6).astype(np.complex64)

    signal_power = np.mean(np.abs(faded)**2)
    noise_power = signal_power / (10**(24/10))
    noise = np.sqrt(noise_power/2) * (
        rng.standard_normal(len(faded)) + 1j*rng.standard_normal(len(faded))
    )

    iq = np.concatenate([
        np.zeros(3000, np.complex64),
        (faded + noise).astype(np.complex64),
        np.zeros(2500, np.complex64),
        (faded + noise).astype(np.complex64),
        np.zeros(3000, np.complex64),
    ])

    cfg = ReceiverConfig(
        stf_threshold=0.55,
        stf_min_plateau=24,
        min_separation_samples=4500,
        min_ltf_template_metric=0.05,
        max_ltf_consistency_error=0.40,
    )
    accepted, rejected = decode_capture(iq, cfg)

    assert len(accepted) >= 1, [r.reason for r in rejected]
    first = accepted[0]
    assert first.beacon.fcs_valid
    assert first.beacon.vendor.valid
    assert first.beacon.vendor.packet_counter == 4321
    assert first.csi.shape == (52,)

    print("PASS")
    print(f"  accepted: {len(accepted)}")
    print(f"  rejected: {len(rejected)}")
    print(f"  counter: {first.beacon.vendor.packet_counter}")
    print(f"  FCS: {first.beacon.fcs_valid}")
    print(f"  Vendor: {first.beacon.vendor.valid}")
    print(f"  CSI shape: {first.csi.shape}")


if __name__ == "__main__":
    main()
