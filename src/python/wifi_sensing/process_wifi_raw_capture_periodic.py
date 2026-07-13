#!/usr/bin/env python3
"""
Periodic WiFi beacon CSI extractor.

This processor is designed for our TX:
  - one beacon every tx-period-ms
  - default 100 ms
  - 20 Msps
  - sparse beacon train

Instead of scanning the whole capture continuously, it:
  1. Finds seed detections.
  2. Estimates beacon phase modulo 100 ms.
  3. Tracks expected beacons at t0 + n*period.
  4. Extracts CSI from each expected beacon window.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import h5py
import numpy as np

try:
    from .wifi_csi import (
        extract_all_csi,
        stf_autocorr_metric,
        local_preamble_refine,
        extract_csi_from_packet,
    )
except ImportError:
    from wifi_csi import (
        extract_all_csi,
        stf_autocorr_metric,
        local_preamble_refine,
        extract_csi_from_packet,
    )


def find_best_phase_from_seeds(
    iq: np.ndarray,
    rate: float,
    period_samples: int,
    seed_seconds: float,
    seed_min_metric: float,
    min_separation_samples: int,
) -> int:
    seed_len = min(len(iq), int(round(seed_seconds * rate)))
    seed_block = np.asarray(iq[:seed_len], dtype=np.complex64)

    seeds = extract_all_csi(
        seed_block,
        sample_rate=rate,
        min_metric=seed_min_metric,
        min_separation_samples=min_separation_samples,
    )

    seed_offsets = [r.offset for r in seeds]

    print(f"Seed detections: {len(seed_offsets)}")

    if not seed_offsets:
        raise RuntimeError(
            "No seed detections found. Try lower --seed-min-metric, higher RX/TX gain, or check antenna port."
        )

    # Candidate phases are seed offsets modulo beacon period.
    candidate_phases = sorted(set(int(o % period_samples) for o in seed_offsets))

    best_phase = candidate_phases[0]
    best_score = -1.0
    best_hits = -1

    # Score each phase by checking periodic windows across the whole capture.
    # We use a fast STF autocorrelation metric locally.
    search_radius = int(round(0.003 * rate))  # +/- 3 ms

    for phase in candidate_phases:
        scores = []
        hits = 0

        n0 = 0
        while phase + n0 * period_samples < len(iq):
            expected = phase + n0 * period_samples

            a = max(0, expected - search_radius)
            b = min(len(iq), expected + search_radius + 4000)

            if b - a < 2000:
                n0 += 1
                continue

            block = np.asarray(iq[a:b], dtype=np.complex64)
            metric = stf_autocorr_metric(block)

            if len(metric):
                m = float(np.max(metric))
                scores.append(m)
                if m > 0.05:
                    hits += 1

            n0 += 1

        if scores:
            score = float(np.median(scores)) + 0.05 * hits
        else:
            score = -1.0

        if score > best_score:
            best_score = score
            best_phase = phase
            best_hits = hits

    print(f"Best phase: {best_phase} samples = {best_phase / rate:.9f} s")
    print(f"Best phase score: {best_score:.4f}")
    print(f"Best phase rough hits: {best_hits}")

    return int(best_phase)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input-npy", required=True)
    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--tx-period-ms", type=float, default=100.0)
    p.add_argument("--seed-seconds", type=float, default=2.0)
    p.add_argument("--seed-min-metric", type=float, default=0.05)
    p.add_argument("--accept-min-metric", type=float, default=0.05)
    p.add_argument("--search-radius-ms", type=float, default=3.0)
    p.add_argument("--output-h5", default="results/wifi_debug/raw_capture_periodic_csi.h5")
    args = p.parse_args()

    iq = np.load(args.input_npy, mmap_mode="r")
    n = len(iq)

    period_samples = int(round(args.tx_period_ms * 1e-3 * args.rate))
    search_radius = int(round(args.search_radius_ms * 1e-3 * args.rate))
    min_separation = int(round(0.050 * args.rate))

    print("Periodic WiFi beacon CSI processing")
    print(f"  input: {args.input_npy}")
    print(f"  samples: {n}")
    print(f"  seconds: {n / args.rate:.3f}")
    print(f"  rate: {args.rate}")
    print(f"  tx_period_ms: {args.tx_period_ms}")
    print(f"  period_samples: {period_samples}")
    print(f"  search_radius_ms: {args.search_radius_ms}")
    print(f"  output: {args.output_h5}")

    phase = find_best_phase_from_seeds(
        iq=iq,
        rate=args.rate,
        period_samples=period_samples,
        seed_seconds=args.seed_seconds,
        seed_min_metric=args.seed_min_metric,
        min_separation_samples=min_separation,
    )

    all_csi = []
    all_offsets = []
    all_metric = []
    all_cfo = []
    all_power = []
    missed = 0

    expected_offsets = list(range(phase, n - 4000, period_samples))

    print(f"Expected beacons in capture: {len(expected_offsets)}")

    for idx, expected in enumerate(expected_offsets):
        a = max(0, expected - search_radius)
        b = min(n, expected + search_radius + 6000)

        block = np.asarray(iq[a:b], dtype=np.complex64)

        metric = stf_autocorr_metric(block)

        if len(metric) == 0:
            missed += 1
            continue

        coarse_local = int(np.argmax(metric))
        coarse_global = a + coarse_local

        refined_global, refine_metric = local_preamble_refine(
            np.asarray(iq[max(0, coarse_global - 200):min(n, coarse_global + 2000)], dtype=np.complex64),
            coarse=200 if coarse_global >= 200 else coarse_global,
            search_before=120,
            search_after=240,
        )

        refined_global = max(0, coarse_global - 200) + refined_global

        if refine_metric < args.accept_min_metric:
            missed += 1
            continue

        pkt = np.asarray(iq[refined_global:refined_global + 2400], dtype=np.complex64)

        try:
            csi, cfo_hz = extract_csi_from_packet(pkt, sample_rate=args.rate)
        except Exception:
            missed += 1
            continue

        rx_power = float(np.mean(np.abs(pkt[:320]) ** 2) + 1e-12)
        rx_power_db = 10 * np.log10(rx_power)

        all_csi.append(csi)
        all_offsets.append(refined_global)
        all_metric.append(refine_metric)
        all_cfo.append(cfo_hz)
        all_power.append(rx_power_db)

        if len(all_csi) % 10 == 0:
            print(
                f"detections={len(all_csi)} "
                f"last_t={refined_global / args.rate:.6f}s "
                f"metric={refine_metric:.3f} "
                f"cfo={cfo_hz:.1f}Hz "
                f"power={rx_power_db:.1f}dB"
            )

    out = Path(args.output_h5)
    out.parent.mkdir(parents=True, exist_ok=True)

    if all_csi:
        csi = np.stack(all_csi).astype(np.complex64)
    else:
        csi = np.zeros((0, 52), dtype=np.complex64)

    offsets = np.asarray(all_offsets, dtype=np.int64)

    with h5py.File(out, "w") as h5:
        h5.create_dataset("csi", data=csi)
        h5.create_dataset("csi_amp", data=np.abs(csi).astype(np.float32))
        h5.create_dataset("csi_phase", data=np.angle(csi).astype(np.float32))
        h5.create_dataset("offset_samples", data=offsets)
        h5.create_dataset("timestamp_sec", data=offsets.astype(np.float64) / args.rate)
        h5.create_dataset("ltf_metric", data=np.asarray(all_metric, dtype=np.float32))
        h5.create_dataset("cfo_hz", data=np.asarray(all_cfo, dtype=np.float32))
        h5.create_dataset("rx_power_db", data=np.asarray(all_power, dtype=np.float32))

        h5.attrs["input_npy"] = args.input_npy
        h5.attrs["rate"] = args.rate
        h5.attrs["tx_period_ms"] = args.tx_period_ms
        h5.attrs["period_samples"] = period_samples
        h5.attrs["phase_samples"] = phase
        h5.attrs["expected_beacons"] = len(expected_offsets)
        h5.attrs["detected_beacons"] = len(all_csi)
        h5.attrs["missed_beacons"] = missed

    print("Done")
    print(f"  expected: {len(expected_offsets)}")
    print(f"  detections: {len(all_csi)}")
    print(f"  missed: {missed}")
    print(f"  saved: {out}")

    if len(offsets) >= 2:
        dt = np.diff(offsets.astype(np.float64) / args.rate)
        print(f"  period mean: {np.mean(dt):.9f} s")
        print(f"  period std:  {np.std(dt):.9f} s")
        print(f"  first periods: {dt[:10]}")
        print(f"  metric mean: {np.mean(all_metric):.4f}")
        print(f"  metric min/max: {np.min(all_metric):.4f} / {np.max(all_metric):.4f}")
        print(f"  cfo mean/std: {np.mean(all_cfo):.1f} / {np.std(all_cfo):.1f} Hz")
        print(f"  rx power mean: {np.mean(all_power):.1f} dB")


if __name__ == "__main__":
    main()
