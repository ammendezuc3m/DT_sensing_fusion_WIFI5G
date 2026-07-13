#!/usr/bin/env python3
"""
Robust offline WiFi beacon CSI extractor using L-LTF self-correlation.

Designed for our TX:
  - 802.11a/g legacy OFDM beacon
  - 20 Msps
  - one beacon every 100 ms
  - L-LTF contains two repeated 64-sample long symbols

Algorithm:
  1. Scan the beginning of the capture using L-LTF repetition.
  2. Estimate the beacon phase modulo 100 ms.
  3. For each expected beacon time, search only a small window.
  4. Refine packet start using L-LTF repetition.
  5. Extract CSI from the L-LTF.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import h5py
import numpy as np

try:
    from .wifi_csi import extract_csi_from_packet
except ImportError:
    from wifi_csi import extract_csi_from_packet


def rolling_sum(x: np.ndarray, win: int) -> np.ndarray:
    if len(x) < win:
        return np.zeros(0, dtype=x.dtype)
    return np.convolve(x, np.ones(win, dtype=x.dtype), mode="valid")


def ltf_metric_for_packet_starts(x: np.ndarray) -> np.ndarray:
    """
    Return metric[p] for candidate packet start p.

    In our generated PPDU:
      packet start = p
      L-STF length = 160
      L-LTF CP length = 32
      first repeated long symbol starts at p + 192
      second repeated long symbol starts at p + 256

    Metric:
      |sum conj(s1) * s2|^2 / (E1 * E2)
    """
    need = 192 + 128
    if len(x) < need:
        return np.zeros(0, dtype=np.float32)

    d0 = 192
    y = x[d0:]

    if len(y) < 128:
        return np.zeros(0, dtype=np.float32)

    a = y[:-64]
    b = y[64:]

    corr_terms = np.conj(a) * b
    e1_terms = np.abs(a) ** 2
    e2_terms = np.abs(b) ** 2

    corr = rolling_sum(corr_terms.astype(np.complex64), 64)
    e1 = rolling_sum(e1_terms.astype(np.float32), 64)
    e2 = rolling_sum(e2_terms.astype(np.float32), 64)

    m = (np.abs(corr) ** 2) / (e1 * e2 + 1e-12)

    # metric index corresponds to packet start p
    return m.astype(np.float32)


def find_peaks_from_metric(
    metric: np.ndarray,
    threshold: float,
    min_separation: int,
) -> list[tuple[int, float]]:
    idx = np.where(metric >= threshold)[0]

    if len(idx) == 0:
        return []

    groups = []
    s = int(idx[0])
    prev = int(idx[0])

    for v in idx[1:]:
        v = int(v)
        if v <= prev + 1:
            prev = v
        else:
            groups.append((s, prev))
            s = v
            prev = v

    groups.append((s, prev))

    peaks = []
    last = -10**18

    for a, b in groups:
        local = a + int(np.argmax(metric[a:b + 1]))
        val = float(metric[local])

        if local - last < min_separation:
            if peaks and val > peaks[-1][1]:
                peaks[-1] = (local, val)
                last = local
            continue

        peaks.append((local, val))
        last = local

    return peaks


def scan_ltf_seeds(
    iq: np.ndarray,
    rate: float,
    seed_seconds: float,
    threshold: float,
    min_separation_samples: int,
    chunk_ms: float = 250.0,
) -> list[tuple[int, float]]:
    n_seed = min(len(iq), int(round(seed_seconds * rate)))
    chunk = int(round(chunk_ms * 1e-3 * rate))
    overlap = 4000

    all_peaks: list[tuple[int, float]] = []
    last_global = -10**18

    start = 0

    while start < n_seed:
        end = min(n_seed, start + chunk + overlap)
        block = np.asarray(iq[start:end], dtype=np.complex64)

        metric = ltf_metric_for_packet_starts(block)
        peaks = find_peaks_from_metric(metric, threshold, min_separation_samples)

        for local, val in peaks:
            global_off = start + local

            if global_off - last_global < min_separation_samples:
                if all_peaks and val > all_peaks[-1][1]:
                    all_peaks[-1] = (global_off, val)
                    last_global = global_off
                continue

            all_peaks.append((global_off, val))
            last_global = global_off

        start += chunk

    return all_peaks


def estimate_phase_from_offsets(
    offsets: list[int],
    period_samples: int,
    bin_width_samples: int,
) -> int:
    phases = np.asarray([o % period_samples for o in offsets], dtype=np.int64)

    if len(phases) == 0:
        raise RuntimeError("No offsets for phase estimation")

    bins = phases // bin_width_samples

    # Also handle wrap-around by duplicated bins.
    unique, counts = np.unique(bins, return_counts=True)
    best_bin = int(unique[np.argmax(counts)])

    mask = (
        (bins == best_bin)
        | (bins == best_bin - 1)
        | (bins == best_bin + 1)
    )

    cluster = phases[mask]

    if len(cluster) == 0:
        cluster = phases

    phase = int(np.median(cluster))

    return phase


def refine_packet_start_ltf(
    iq: np.ndarray,
    expected_start: int,
    search_radius_samples: int,
) -> tuple[int, float]:
    a = max(0, expected_start - search_radius_samples)
    b = min(len(iq), expected_start + search_radius_samples + 4000)

    block = np.asarray(iq[a:b], dtype=np.complex64)
    metric = ltf_metric_for_packet_starts(block)

    if len(metric) == 0:
        return expected_start, 0.0

    local = int(np.argmax(metric))
    val = float(metric[local])

    return a + local, val


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input-npy", required=True)
    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--tx-period-ms", type=float, default=100.0)
    p.add_argument("--seed-seconds", type=float, default=2.0)
    p.add_argument("--seed-threshold", type=float, default=0.25)
    p.add_argument("--accept-threshold", type=float, default=0.25)
    p.add_argument("--search-radius-ms", type=float, default=5.0)
    p.add_argument("--phase-bin-ms", type=float, default=2.0)
    p.add_argument("--output-h5", default="results/wifi_debug/raw_capture_ltf_tracker_csi.h5")
    args = p.parse_args()

    iq = np.load(args.input_npy, mmap_mode="r")
    n = len(iq)

    period_samples = int(round(args.tx_period_ms * 1e-3 * args.rate))
    search_radius = int(round(args.search_radius_ms * 1e-3 * args.rate))
    min_separation = int(round(0.060 * args.rate))
    phase_bin = int(round(args.phase_bin_ms * 1e-3 * args.rate))

    print("WiFi L-LTF tracker processing")
    print(f"  input: {args.input_npy}")
    print(f"  samples: {n}")
    print(f"  seconds: {n / args.rate:.3f}")
    print(f"  rate: {args.rate}")
    print(f"  tx_period_ms: {args.tx_period_ms}")
    print(f"  period_samples: {period_samples}")
    print(f"  seed_threshold: {args.seed_threshold}")
    print(f"  accept_threshold: {args.accept_threshold}")
    print(f"  search_radius_ms: {args.search_radius_ms}")
    print(f"  output: {args.output_h5}")

    seeds = scan_ltf_seeds(
        iq=iq,
        rate=args.rate,
        seed_seconds=args.seed_seconds,
        threshold=args.seed_threshold,
        min_separation_samples=min_separation,
    )

    print(f"Seed L-LTF detections: {len(seeds)}")

    if len(seeds) == 0:
        raise SystemExit("No L-LTF seeds found. Try --seed-threshold 0.10 or check gain/antenna.")

    seed_offsets = [x[0] for x in seeds]
    seed_metrics = [x[1] for x in seeds]

    print("First seed offsets/sec/metric:")
    for off, m in seeds[:10]:
        print(f"  {off}  {off / args.rate:.6f}s  metric={m:.3f}")

    phase = estimate_phase_from_offsets(seed_offsets, period_samples, phase_bin)

    print(f"Estimated phase: {phase} samples = {phase / args.rate:.9f} s")

    # Build expected packet starts in [0, n).
    expected = []
    k_min = int(np.floor((0 - phase) / period_samples)) - 1
    k = k_min

    while True:
        e = phase + k * period_samples
        if e >= n - 4000:
            break
        if e >= 0:
            expected.append(int(e))
        k += 1

    print(f"Expected beacons: {len(expected)}")

    all_csi = []
    all_offsets = []
    all_metric = []
    all_cfo = []
    all_power = []
    all_expected = []
    all_error_samples = []
    missed = 0

    for i, e in enumerate(expected):
        off, m = refine_packet_start_ltf(iq, e, search_radius)

        if m < args.accept_threshold:
            missed += 1
            continue

        pkt = np.asarray(iq[off:off + 2400], dtype=np.complex64)

        if len(pkt) < 320:
            missed += 1
            continue

        try:
            csi, cfo_hz = extract_csi_from_packet(pkt, sample_rate=args.rate)
        except Exception:
            missed += 1
            continue

        rx_power = float(np.mean(np.abs(pkt[:320]) ** 2) + 1e-12)
        rx_power_db = 10 * np.log10(rx_power)

        all_csi.append(csi)
        all_offsets.append(off)
        all_metric.append(m)
        all_cfo.append(cfo_hz)
        all_power.append(rx_power_db)
        all_expected.append(e)
        all_error_samples.append(off - e)

        if len(all_csi) % 10 == 0:
            print(
                f"detections={len(all_csi)} "
                f"t={off / args.rate:.6f}s "
                f"metric={m:.3f} "
                f"err_samples={off - e} "
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
    expected_arr = np.asarray(all_expected, dtype=np.int64)
    err_arr = np.asarray(all_error_samples, dtype=np.int64)

    with h5py.File(out, "w") as h5:
        h5.create_dataset("csi", data=csi)
        h5.create_dataset("csi_amp", data=np.abs(csi).astype(np.float32))
        h5.create_dataset("csi_phase", data=np.angle(csi).astype(np.float32))
        h5.create_dataset("offset_samples", data=offsets)
        h5.create_dataset("expected_offset_samples", data=expected_arr)
        h5.create_dataset("timing_error_samples", data=err_arr)
        h5.create_dataset("timestamp_sec", data=offsets.astype(np.float64) / args.rate)
        h5.create_dataset("ltf_metric", data=np.asarray(all_metric, dtype=np.float32))
        h5.create_dataset("cfo_hz", data=np.asarray(all_cfo, dtype=np.float32))
        h5.create_dataset("rx_power_db", data=np.asarray(all_power, dtype=np.float32))

        h5.attrs["input_npy"] = args.input_npy
        h5.attrs["rate"] = args.rate
        h5.attrs["tx_period_ms"] = args.tx_period_ms
        h5.attrs["period_samples"] = period_samples
        h5.attrs["phase_samples"] = phase
        h5.attrs["expected_beacons"] = len(expected)
        h5.attrs["detected_beacons"] = len(all_csi)
        h5.attrs["missed_beacons"] = missed
        h5.attrs["seed_threshold"] = args.seed_threshold
        h5.attrs["accept_threshold"] = args.accept_threshold

    print("Done")
    print(f"  expected: {len(expected)}")
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
        print(f"  timing err mean/std samples: {np.mean(err_arr):.1f} / {np.std(err_arr):.1f}")


if __name__ == "__main__":
    main()
