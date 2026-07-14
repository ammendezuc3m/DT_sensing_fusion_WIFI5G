#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .wlan_rx import ReceiverConfig, decode_capture


def parse_oui(value: str) -> bytes:
    raw = bytes.fromhex(value.replace(":", "").replace("-", ""))
    if len(raw) != 3:
        raise argparse.ArgumentTypeError("OUI must contain 3 bytes")
    return raw


def load_iq(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        return np.asarray(np.load(path), dtype=np.complex64)
    d = np.load(path, allow_pickle=False)
    for key in ("iq", "samples", "waveform"):
        if key in d:
            return np.asarray(d[key], dtype=np.complex64)
    raise ValueError("NPZ must contain iq, samples or waveform")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--stf-threshold", type=float, default=0.65)
    p.add_argument("--stf-min-plateau", type=int, default=48)
    p.add_argument("--min-separation-samples", type=int, default=4800)
    p.add_argument("--min-ltf-template-metric", type=float, default=0.08)
    p.add_argument("--max-ltf-consistency-error", type=float, default=0.35)

    p.add_argument("--expected-ssid", default="SENSING_WIFI")
    p.add_argument("--expected-bssid", default="02:11:22:33:44:55")
    p.add_argument("--expected-oui", type=parse_oui, default=b"\x02\x11\x22")
    p.add_argument("--expected-vendor-type", type=int, default=1)
    p.add_argument("--expected-magic", default="ALBSENS")
    p.add_argument("--expected-version", type=int, default=1)
    p.add_argument("--expected-transmitter-id", type=int, default=1)
    p.add_argument("--expected-experiment-id", type=int, default=1)

    p.add_argument("--output", default="results/wifi_rx_v2")
    p.add_argument("--verbose-rejects", action="store_true")
    args = p.parse_args()

    iq = load_iq(Path(args.input))
    cfg = ReceiverConfig(
        sample_rate=args.rate,
        stf_threshold=args.stf_threshold,
        stf_min_plateau=args.stf_min_plateau,
        min_separation_samples=args.min_separation_samples,
        min_ltf_template_metric=args.min_ltf_template_metric,
        max_ltf_consistency_error=args.max_ltf_consistency_error,
        expected_ssid=args.expected_ssid,
        expected_bssid=args.expected_bssid,
        expected_oui=args.expected_oui,
        expected_vendor_type=args.expected_vendor_type,
        expected_magic=args.expected_magic.encode("ascii"),
        expected_version=args.expected_version,
        expected_transmitter_id=args.expected_transmitter_id,
        expected_experiment_id=args.expected_experiment_id,
    )

    accepted, rejected = decode_capture(iq, cfg)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    if accepted:
        csi = np.stack([x.csi for x in accepted]).astype(np.complex64)
        offsets = np.array([x.offset for x in accepted], np.int64)
        counters = np.array(
            [x.beacon.vendor.packet_counter for x in accepted],
            np.uint32,
        )
    else:
        csi = np.empty((0, 52), np.complex64)
        offsets = np.empty(0, np.int64)
        counters = np.empty(0, np.uint32)

    np.savez_compressed(
        out / "accepted_beacons.npz",
        csi=csi,
        offsets=offsets,
        packet_counters=counters,
        sample_rate_hz=np.array([args.rate]),
    )

    report = {
        "input": args.input,
        "samples": int(len(iq)),
        "duration_sec": float(len(iq) / args.rate),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "accepted_packets": [x.to_json() for x in accepted],
        "rejects": [r.__dict__ for r in rejected],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    for item in accepted:
        print(
            f"ACCEPT offset={item.offset} "
            f"counter={item.beacon.vendor.packet_counter} "
            f"seq={item.beacon.sequence_number} "
            f"cfo={item.total_cfo_hz:.1f}Hz "
            f"ltf={item.ltf_template_metric:.3f} "
            f"err={item.ltf_consistency_error:.4f}"
        )

    if args.verbose_rejects:
        for item in rejected:
            print(
                f"REJECT offset={item.coarse_offset} "
                f"stf={item.stf_metric:.3f}: {item.reason}"
            )

    print()
    print("Python WLAN RX v2")
    print(f"  duration: {len(iq)/args.rate:.3f} s")
    print(f"  accepted: {len(accepted)}")
    print(f"  rejected: {len(rejected)}")
    print(f"  CSI shape: {csi.shape}")
    print(f"  output: {out}")


if __name__ == "__main__":
    main()
