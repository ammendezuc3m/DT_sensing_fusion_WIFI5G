#!/usr/bin/env python3
"""
Process raw WiFi IQ capture and extract CSI offline.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import h5py
import numpy as np

try:
    from .wifi_csi import extract_all_csi
except ImportError:
    from wifi_csi import extract_all_csi


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input-npy", required=True)
    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--min-metric", type=float, default=0.25)
    p.add_argument("--min-separation-ms", type=float, default=50.0)
    p.add_argument("--chunk-ms", type=float, default=250.0)
    p.add_argument("--output-h5", default="results/wifi_debug/raw_capture_csi.h5")
    args = p.parse_args()

    iq = np.load(args.input_npy, mmap_mode="r")
    n = len(iq)

    chunk = int(round(args.chunk_ms * 1e-3 * args.rate))
    overlap = int(round(0.002 * args.rate))
    min_sep = int(round(args.min_separation_ms * 1e-3 * args.rate))

    print("Offline WiFi CSI processing")
    print(f"  input: {args.input_npy}")
    print(f"  samples: {n}")
    print(f"  seconds: {n / args.rate:.3f}")
    print(f"  min_metric: {args.min_metric}")
    print(f"  output: {args.output_h5}")

    all_csi = []
    all_offsets = []
    all_metric = []
    all_cfo = []
    all_power = []

    last_global_offset = -10**18

    start = 0

    while start < n:
        end = min(n, start + chunk + overlap)
        block = np.asarray(iq[start:end], dtype=np.complex64)

        results = extract_all_csi(
            block,
            sample_rate=args.rate,
            min_metric=args.min_metric,
            min_separation_samples=min_sep,
        )

        for r in results:
            global_off = start + r.offset

            if global_off - last_global_offset < min_sep:
                continue

            all_csi.append(r.csi)
            all_offsets.append(global_off)
            all_metric.append(r.metric)
            all_cfo.append(r.cfo_hz)
            all_power.append(r.rx_power_db)
            last_global_offset = global_off

        print(f"processed {end / args.rate:.3f} s / {n / args.rate:.3f} s, detections={len(all_csi)}")

        start += chunk

    out = Path(args.output_h5)
    out.parent.mkdir(parents=True, exist_ok=True)

    if all_csi:
        csi = np.stack(all_csi).astype(np.complex64)
    else:
        csi = np.zeros((0, 52), dtype=np.complex64)

    with h5py.File(out, "w") as h5:
        h5.create_dataset("csi", data=csi)
        h5.create_dataset("csi_amp", data=np.abs(csi).astype(np.float32))
        h5.create_dataset("csi_phase", data=np.angle(csi).astype(np.float32))
        h5.create_dataset("offset_samples", data=np.asarray(all_offsets, dtype=np.int64))
        h5.create_dataset("timestamp_sec", data=np.asarray(all_offsets, dtype=np.float64) / args.rate)
        h5.create_dataset("ltf_metric", data=np.asarray(all_metric, dtype=np.float32))
        h5.create_dataset("cfo_hz", data=np.asarray(all_cfo, dtype=np.float32))
        h5.create_dataset("rx_power_db", data=np.asarray(all_power, dtype=np.float32))

    print("Done")
    print(f"  detections: {len(all_csi)}")
    print(f"  saved: {out}")

    if len(all_offsets) >= 2:
        dt = np.diff(np.asarray(all_offsets) / args.rate)
        print(f"  period mean: {np.mean(dt):.6f} s")
        print(f"  period std:  {np.std(dt):.6f} s")
        print(f"  first periods: {dt[:10]}")


if __name__ == "__main__":
    main()
