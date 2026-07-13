#!/usr/bin/env python3
"""
Periodic offline RX for the DMG/802.11bf-inspired sensing waveform.

This processor validates the TX/RX chain before implementing the online RX.

It estimates the burst phase and then tracks the expected periodic structure:

    phase + k * period_samples

For each expected burst, it refines timing around the expected position using
CEF Golay correlation and extracts CIR/CFR.

This is intended for consistent dataset generation:
    - fixed TX period
    - stable burst indexing
    - CIR per burst
    - CFR per burst
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from waveform import load_dmg_sensing_waveform_npz
from process_dmg_raw_capture import (
    extract_cir_from_cef,
    greedy_energy_seeds,
    refine_with_cef,
)


def circular_distance_samples(a: np.ndarray, b: int, period: int) -> np.ndarray:
    d = np.abs(a.astype(np.int64) - int(b))
    return np.minimum(d, period - d)


def circular_weighted_mean_phase(
    phases: np.ndarray,
    weights: np.ndarray,
    period: int,
) -> int:
    if len(phases) == 0:
        raise ValueError("No phases for circular mean")

    angles = 2.0 * np.pi * phases.astype(np.float64) / float(period)
    z = np.sum(weights.astype(np.float64) * np.exp(1j * angles))

    if np.abs(z) < 1e-12:
        return int(np.round(np.median(phases))) % period

    angle = float(np.angle(z))

    if angle < 0:
        angle += 2.0 * np.pi

    return int(np.round(angle / (2.0 * np.pi) * period)) % period


def estimate_phase_from_detections(
    start_samples: np.ndarray,
    metrics: np.ndarray,
    period_samples: int,
    phase_bin_ms: float,
    phase_cluster_ms: float,
    fs: float,
) -> dict[str, Any]:
    """
    Estimate burst phase modulo period_samples.

    The real TX bursts should share a common modulo phase, even if the initial
    coarse detections are noisy.
    """
    phases = (start_samples % period_samples).astype(np.int64)

    bin_samples = max(1, int(round(phase_bin_ms * 1e-3 * fs)))
    cluster_samples = max(bin_samples, int(round(phase_cluster_ms * 1e-3 * fs)))

    nbins = int(np.ceil(period_samples / bin_samples))
    hist = np.zeros(nbins, dtype=np.float64)

    for ph, m in zip(phases, metrics):
        b = int(ph // bin_samples)
        b = min(max(b, 0), nbins - 1)
        hist[b] += 1.0 + 0.01 * float(m)

    best_bin = int(np.argmax(hist))
    phase_center = int((best_bin + 0.5) * bin_samples) % period_samples

    dist = circular_distance_samples(phases, phase_center, period_samples)
    in_cluster = dist <= cluster_samples

    if int(np.sum(in_cluster)) < 2:
        best = int(np.argmax(metrics))
        phase_est = int(phases[best])
        used_count = 1
    else:
        w = metrics[in_cluster].astype(np.float64)
        w = w - np.min(w) + 1e-6
        phase_est = circular_weighted_mean_phase(phases[in_cluster], w, period_samples)
        used_count = int(np.sum(in_cluster))

    return {
        "phase_samples": int(phase_est),
        "phase_seconds": float(phase_est / fs),
        "phase_ms": float(phase_est / fs * 1000.0),
        "phase_bin_ms": float(phase_bin_ms),
        "phase_cluster_ms": float(phase_cluster_ms),
        "bin_samples": int(bin_samples),
        "cluster_samples": int(cluster_samples),
        "best_bin": int(best_bin),
        "cluster_used_count": int(used_count),
        "num_input_detections": int(len(start_samples)),
    }


def expected_starts_from_phase(
    phase_samples: int,
    period_samples: int,
    total_samples: int,
    waveform_len: int,
) -> np.ndarray:
    starts: list[int] = []

    s = int(phase_samples)

    while s + waveform_len < total_samples:
        starts.append(s)
        s += period_samples

    return np.array(starts, dtype=np.int64)


def main() -> None:
    p = argparse.ArgumentParser()

    p.add_argument("--input-npy", required=True)
    p.add_argument(
        "--waveform-npz",
        default="results/wlan_sensing_dmg/dmg_like_sensing_ppdu_v1.npz",
    )
    p.add_argument(
        "--output-npz",
        default="results/wlan_sensing_dmg/raw_dmg_like_tx_5s_periodic_processed.npz",
    )
    p.add_argument(
        "--output-csv",
        default="results/wlan_sensing_dmg/raw_dmg_like_tx_5s_periodic_detections.csv",
    )

    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--tx-period-ms", type=float, default=100.0)
    p.add_argument("--expected-bursts", type=int, default=0)
    p.add_argument("--extra-seeds", type=int, default=50)

    # Initial phase estimation.
    p.add_argument("--energy-win-ms", type=float, default=1.0)
    p.add_argument("--energy-min-sep-ms", type=float, default=70.0)
    p.add_argument("--initial-search-before-ms", type=float, default=4.0)
    p.add_argument("--initial-search-after-ms", type=float, default=2.0)
    p.add_argument("--phase-bin-ms", type=float, default=2.0)
    p.add_argument("--phase-cluster-ms", type=float, default=15.0)

    # Periodic tracking.
    p.add_argument("--track-search-radius-ms", type=float, default=25.0)
    p.add_argument("--min-metric", type=float, default=0.0)

    args = p.parse_args()

    x = np.load(args.input_npy, mmap_mode="r")
    total_samples = len(x)
    duration_s = total_samples / args.rate

    wf = load_dmg_sensing_waveform_npz(args.waveform_npz)
    meta = wf["metadata"]
    field_map = meta["field_map"]

    waveform_len = int(meta["waveform_samples"])
    cef_start = int(field_map["cef"]["start"])
    cef_stop = int(field_map["cef"]["stop"])

    cef_ga = wf["cef_ga"].astype(np.float32)
    cef_gb = wf["cef_gb"].astype(np.float32)

    period_samples = int(round(args.tx_period_ms * 1e-3 * args.rate))

    if args.expected_bursts > 0:
        expected = int(args.expected_bursts)
    else:
        expected = int(round(duration_s / (args.tx_period_ms / 1000.0)))

    seed_count = expected + max(0, int(args.extra_seeds))

    print("DMG-like periodic RAW offline processor")
    print("  input:", args.input_npy)
    print("  waveform:", args.waveform_npz)
    print("  samples:", total_samples)
    print("  duration_s: %.6f" % duration_s)
    print("  period_samples:", period_samples)
    print("  expected bursts:", expected)
    print("  initial seed count:", seed_count)
    print("  waveform samples:", waveform_len)
    print("  CEF start/stop: %d/%d" % (cef_start, cef_stop))

    # Step 1: coarse energy seeds + CEF refinement only to estimate phase.
    seed_samples, energy_pwr_db, selected_windows = greedy_energy_seeds(
        x=x,
        fs=args.rate,
        expected=seed_count,
        energy_win_ms=args.energy_win_ms,
        min_sep_ms=args.energy_min_sep_ms,
    )

    phase_dets: list[dict[str, Any]] = []

    for i, seed in enumerate(seed_samples):
        d = refine_with_cef(
            x=x,
            seed_sample=int(seed),
            cef_ga=cef_ga,
            cef_gb=cef_gb,
            cef_start=cef_start,
            cef_stop=cef_stop,
            waveform_len=waveform_len,
            fs=args.rate,
            search_before_ms=args.initial_search_before_ms,
            search_after_ms=args.initial_search_after_ms,
        )

        if d is None:
            continue

        d["initial_seed_index"] = int(i)
        phase_dets.append(d)

    if len(phase_dets) < 2:
        raise SystemExit("Not enough phase detections to estimate periodic phase.")

    phase_starts = np.array([d["start_sample"] for d in phase_dets], dtype=np.int64)
    phase_metrics = np.array([d["metric"] for d in phase_dets], dtype=np.float32)

    phase_info = estimate_phase_from_detections(
        start_samples=phase_starts,
        metrics=phase_metrics,
        period_samples=period_samples,
        phase_bin_ms=args.phase_bin_ms,
        phase_cluster_ms=args.phase_cluster_ms,
        fs=args.rate,
    )

    print("Phase estimate")
    print("  phase_samples:", phase_info["phase_samples"])
    print("  phase_ms: %.6f" % phase_info["phase_ms"])
    print("  cluster_used_count:", phase_info["cluster_used_count"])
    print("  input phase detections:", phase_info["num_input_detections"])

    expected_starts = expected_starts_from_phase(
        phase_samples=int(phase_info["phase_samples"]),
        period_samples=period_samples,
        total_samples=total_samples,
        waveform_len=waveform_len,
    )

    if len(expected_starts) > expected:
        expected_starts = expected_starts[:expected]

    print("  expected starts from phase:", len(expected_starts))
    print("  first expected times s:", expected_starts[:20] / args.rate)

    # Step 2: periodic tracking. For each expected burst, refine locally with CEF.
    detections: list[dict[str, Any]] = []
    radius_ms = float(args.track_search_radius_ms)

    for i, exp_s in enumerate(expected_starts):
        d = refine_with_cef(
            x=x,
            seed_sample=int(exp_s),
            cef_ga=cef_ga,
            cef_gb=cef_gb,
            cef_start=cef_start,
            cef_stop=cef_stop,
            waveform_len=waveform_len,
            fs=args.rate,
            search_before_ms=radius_ms,
            search_after_ms=radius_ms,
        )

        if d is None:
            continue

        d["burst_index"] = int(i)
        d["expected_start_sample"] = int(exp_s)
        d["timing_error_samples"] = int(d["start_sample"] - exp_s)
        d["timing_error_us"] = float((d["start_sample"] - exp_s) / args.rate * 1e6)

        if float(d["metric"]) >= args.min_metric:
            detections.append(d)

    print("  periodic refined detections:", len(detections))

    if not detections:
        raise SystemExit("No periodic detections found.")

    start_samples = np.array([d["start_sample"] for d in detections], dtype=np.int64)
    expected_samples = np.array([d["expected_start_sample"] for d in detections], dtype=np.int64)
    timing_error_samples = np.array([d["timing_error_samples"] for d in detections], dtype=np.int64)
    timing_error_us = np.array([d["timing_error_us"] for d in detections], dtype=np.float32)

    metrics = np.array([d["metric"] for d in detections], dtype=np.float32)
    raw_scores = np.array([d["raw_score"] for d in detections], dtype=np.float32)
    burst_power_db = np.array([d["burst_power_db"] for d in detections], dtype=np.float32)
    cef_power_db = np.array([d["cef_power_db"] for d in detections], dtype=np.float32)

    times_s = start_samples / args.rate
    expected_times_s = expected_samples / args.rate

    periods = np.diff(times_s)
    expected_periods = np.diff(expected_times_s)

    # Step 3: extract CIR/CFR.
    cirs = []

    for s in start_samples:
        cir = extract_cir_from_cef(
            x=x,
            start_sample=int(s),
            cef_ga=cef_ga,
            cef_gb=cef_gb,
            cef_start=cef_start,
        )
        cirs.append(cir)

    cirs = np.stack(cirs, axis=0).astype(np.complex64)
    cfr = np.fft.fftshift(np.fft.fft(cirs, n=1024, axis=1), axes=1).astype(np.complex64)

    print("Periodic detection summary")
    print("  detections:", len(detections))
    print("  first refined times s:", times_s[:20])
    print("  first expected times s:", expected_times_s[:20])

    if len(periods) > 0:
        print("  refined period mean s: %.9f" % float(np.mean(periods)))
        print("  refined period std s: %.9f" % float(np.std(periods)))
        print("  expected period mean s: %.9f" % float(np.mean(expected_periods)))
        print("  expected period std s: %.9f" % float(np.std(expected_periods)))

    print("  timing error us mean: %.3f" % float(np.mean(timing_error_us)))
    print("  timing error us std: %.3f" % float(np.std(timing_error_us)))
    print(
        "  timing error us min/max: %.3f / %.3f"
        % (float(np.min(timing_error_us)), float(np.max(timing_error_us)))
    )

    print("  metric mean: %.6e" % float(np.mean(metrics)))
    print(
        "  metric min/max: %.6e / %.6e"
        % (float(np.min(metrics)), float(np.max(metrics)))
    )
    print("  burst power dB mean: %.3f" % float(np.mean(burst_power_db)))
    print(
        "  burst power dB min/max: %.3f / %.3f"
        % (float(np.min(burst_power_db)), float(np.max(burst_power_db)))
    )
    print("  CIR shape:", cirs.shape)
    print("  CFR shape:", cfr.shape)

    out = Path(args.output_npz)
    out.parent.mkdir(parents=True, exist_ok=True)

    result_meta = {
        "schema_version": "dmg_like_periodic_raw_offline_processing_v1",
        "input_npy": args.input_npy,
        "waveform_npz": args.waveform_npz,
        "sample_rate_hz": args.rate,
        "duration_s": duration_s,
        "tx_period_ms": args.tx_period_ms,
        "period_samples": period_samples,
        "expected_bursts": expected,
        "detections": int(len(detections)),
        "phase_info": phase_info,
        "waveform_metadata": meta,
        "processing_args": vars(args),
    }

    np.savez_compressed(
        out,
        start_samples=start_samples,
        expected_start_samples=expected_samples,
        timing_error_samples=timing_error_samples,
        timing_error_us=timing_error_us,
        times_s=times_s.astype(np.float64),
        expected_times_s=expected_times_s.astype(np.float64),
        metrics=metrics,
        raw_scores=raw_scores,
        burst_power_db=burst_power_db,
        cef_power_db=cef_power_db,
        cir=cirs,
        cfr=cfr,
        metadata_json=json.dumps(result_meta, indent=2),
    )

    csv_path = Path(args.output_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "idx",
                "time_s",
                "expected_time_s",
                "start_sample",
                "expected_start_sample",
                "timing_error_samples",
                "timing_error_us",
                "metric",
                "raw_score",
                "burst_power_db",
                "cef_power_db",
            ]
        )

        for i in range(len(detections)):
            w.writerow(
                [
                    i,
                    "%.9f" % float(times_s[i]),
                    "%.9f" % float(expected_times_s[i]),
                    int(start_samples[i]),
                    int(expected_samples[i]),
                    int(timing_error_samples[i]),
                    "%.6f" % float(timing_error_us[i]),
                    "%.9e" % float(metrics[i]),
                    "%.9e" % float(raw_scores[i]),
                    "%.6f" % float(burst_power_db[i]),
                    "%.6f" % float(cef_power_db[i]),
                ]
            )

    print("Saved NPZ:", out)
    print("Saved CSV:", csv_path)


if __name__ == "__main__":
    main()
