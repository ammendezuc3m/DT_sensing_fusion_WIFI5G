#!/usr/bin/env python3
"""
Offline decoder for the controlled WiFi sensing beacons.

Input:
  - .npy containing complex64 IQ, or
  - .npz containing an `iq` array.

Only packets that pass the complete identity chain are accepted:
  L-STF -> timing/CFO -> L-LTF CSI -> L-SIG -> DATA -> FCS -> BSSID -> Vendor IE.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .wifi_rx_phy import decode_candidate, detect_stf_candidates
except ImportError:
    from wifi_rx_phy import decode_candidate, detect_stf_candidates


def load_iq(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    if path.suffix.lower() == ".npy":
        return np.load(path).astype(np.complex64), {}

    data = np.load(path, allow_pickle=False)
    if "iq" in data:
        iq = data["iq"]
    elif "samples" in data:
        iq = data["samples"]
    else:
        raise ValueError("NPZ must contain `iq` or `samples`")

    metadata: dict[str, Any] = {}
    if "metadata_json" in data:
        metadata = json.loads(str(data["metadata_json"][0]))
    return np.asarray(iq, dtype=np.complex64), metadata


def parse_hex3(value: str) -> bytes:
    raw = bytes.fromhex(value.replace(":", "").replace("-", ""))
    if len(raw) != 3:
        raise argparse.ArgumentTypeError("OUI must be exactly 3 bytes")
    return raw


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--rate", type=float, default=20e6)

    p.add_argument("--stf-threshold", type=float, default=0.72)
    p.add_argument("--stf-min-plateau", type=int, default=48)
    p.add_argument("--min-preamble-metric", type=float, default=0.20)
    p.add_argument("--max-ltf-consistency-error", type=float, default=0.20)
    p.add_argument("--min-separation-samples", type=int, default=800)
    p.add_argument("--max-candidates", type=int, default=0)

    p.add_argument("--expected-ssid", default="SENSING_WIFI")
    p.add_argument("--expected-bssid", default="02:11:22:33:44:55")
    p.add_argument("--expected-oui", type=parse_hex3, default=b"\x02\x11\x22")
    p.add_argument("--expected-vendor-type", type=int, default=1)
    p.add_argument("--expected-magic", default="ALBSENS")
    p.add_argument("--expected-version", type=int, default=1)
    p.add_argument("--expected-transmitter-id", type=int, default=1)
    p.add_argument("--expected-experiment-id", type=int, default=1)

    p.add_argument("--output-npz", default="results/wifi_rx/accepted_beacons.npz")
    p.add_argument("--output-json", default="results/wifi_rx/accepted_beacons.json")
    p.add_argument("--output-csv", default="results/wifi_rx/accepted_beacons.csv")
    p.add_argument("--verbose-rejects", action="store_true")
    args = p.parse_args()

    input_path = Path(args.input)
    iq, source_metadata = load_iq(input_path)

    candidates = detect_stf_candidates(
        iq,
        sample_rate=args.rate,
        threshold=args.stf_threshold,
        min_plateau_samples=args.stf_min_plateau,
        min_separation_samples=args.min_separation_samples,
    )
    if args.max_candidates > 0:
        candidates = candidates[:args.max_candidates]

    accepted = []
    rejects: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates):
        try:
            result = decode_candidate(
                iq,
                candidate,
                sample_rate=args.rate,
                min_preamble_metric=args.min_preamble_metric,
                max_ltf_consistency_error=args.max_ltf_consistency_error,
                expected_bssid=args.expected_bssid,
                expected_ssid=args.expected_ssid,
                expected_oui=args.expected_oui,
                expected_vendor_type=args.expected_vendor_type,
                expected_magic=args.expected_magic.encode("ascii"),
                expected_version=args.expected_version,
                expected_transmitter_id=args.expected_transmitter_id,
                expected_experiment_id=args.expected_experiment_id,
            )
            accepted.append(result)
            vendor = result.mac.vendor
            print(
                f"ACCEPT candidate={index} offset={result.packet_offset} "
                f"counter={vendor.packet_counter} seq={result.mac.sequence_number} "
                f"cfo={result.total_cfo_hz:.1f}Hz "
                f"stf={result.stf_metric:.3f} preamble={result.preamble_metric:.3f} "
                f"ltf_err={result.ltf_consistency_error:.4f}"
            )
        except Exception as exc:
            row = {
                "candidate_index": index,
                "coarse_offset": candidate.coarse_offset,
                "stf_metric": candidate.stf_metric,
                "coarse_cfo_hz": candidate.coarse_cfo_hz,
                "reason": str(exc),
            }
            rejects.append(row)
            if args.verbose_rejects:
                print(
                    f"REJECT candidate={index} offset={candidate.coarse_offset} "
                    f"metric={candidate.stf_metric:.3f}: {exc}"
                )

    counters = [
        int(x.mac.vendor.packet_counter)
        for x in accepted
        if x.mac.vendor.packet_counter is not None
    ]
    missing = 0
    duplicates = 0
    out_of_order = 0
    for previous, current in zip(counters, counters[1:]):
        if current == previous:
            duplicates += 1
        elif current > previous:
            missing += max(0, current - previous - 1)
        else:
            out_of_order += 1

    output_npz = Path(args.output_npz)
    output_json = Path(args.output_json)
    output_csv = Path(args.output_csv)
    for path in (output_npz, output_json, output_csv):
        path.parent.mkdir(parents=True, exist_ok=True)

    if accepted:
        csi = np.stack([x.csi for x in accepted]).astype(np.complex64)
        offsets = np.asarray([x.packet_offset for x in accepted], dtype=np.int64)
        packet_counters = np.asarray(counters, dtype=np.uint32)
    else:
        csi = np.empty((0, 52), dtype=np.complex64)
        offsets = np.empty(0, dtype=np.int64)
        packet_counters = np.empty(0, dtype=np.uint32)

    np.savez_compressed(
        output_npz,
        csi=csi,
        offsets=offsets,
        packet_counters=packet_counters,
        sample_rate_hz=np.asarray([args.rate], dtype=np.float64),
    )

    report = {
        "input": str(input_path),
        "input_samples": int(len(iq)),
        "duration_sec": float(len(iq) / args.rate),
        "source_metadata": source_metadata,
        "candidates": len(candidates),
        "accepted": len(accepted),
        "rejected": len(rejects),
        "missing_counters": int(missing),
        "duplicate_counters": int(duplicates),
        "out_of_order_counters": int(out_of_order),
        "accepted_packets": [x.json_dict() for x in accepted],
        "rejects": rejects,
    }
    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "packet_offset",
            "packet_counter",
            "sequence_number",
            "total_cfo_hz",
            "rx_power_dbfs",
            "stf_metric",
            "preamble_metric",
            "ltf_consistency_error",
            "ssid",
            "bssid",
            "fcs_valid",
            "vendor_valid",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in accepted:
            writer.writerow({
                "packet_offset": item.packet_offset,
                "packet_counter": item.mac.vendor.packet_counter,
                "sequence_number": item.mac.sequence_number,
                "total_cfo_hz": item.total_cfo_hz,
                "rx_power_dbfs": item.rx_power_dbfs,
                "stf_metric": item.stf_metric,
                "preamble_metric": item.preamble_metric,
                "ltf_consistency_error": item.ltf_consistency_error,
                "ssid": item.mac.ssid,
                "bssid": item.mac.bssid,
                "fcs_valid": item.mac.fcs_valid,
                "vendor_valid": item.mac.vendor.valid,
            })

    print()
    print("Offline WiFi RX summary")
    print(f"  IQ samples: {len(iq)}")
    print(f"  duration: {len(iq) / args.rate:.3f} s")
    print(f"  STF candidates: {len(candidates)}")
    print(f"  accepted our beacons: {len(accepted)}")
    print(f"  rejected candidates: {len(rejects)}")
    print(f"  missing counters: {missing}")
    print(f"  duplicate counters: {duplicates}")
    print(f"  out-of-order counters: {out_of_order}")
    print(f"  NPZ: {output_npz}")
    print(f"  JSON: {output_json}")
    print(f"  CSV: {output_csv}")


if __name__ == "__main__":
    main()
