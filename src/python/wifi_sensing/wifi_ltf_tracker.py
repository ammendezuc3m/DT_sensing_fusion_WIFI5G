#!/usr/bin/env python3
"""
L-LTF-based WiFi beacon timing tracker.

Works with the generated 802.11a/g legacy OFDM beacon:
  L-STF = 160 samples
  L-LTF = 160 samples
  L-LTF repeated long symbols start at packet_start + 192 and +256
"""

from __future__ import annotations

import numpy as np


def rolling_sum(x: np.ndarray, win: int) -> np.ndarray:
    if len(x) < win:
        return np.zeros(0, dtype=x.dtype)
    return np.convolve(x, np.ones(win, dtype=x.dtype), mode="valid")


def ltf_metric_for_packet_starts(x: np.ndarray) -> np.ndarray:
    """
    Metric[p] for packet start p, based on L-LTF repeated 64-sample symbols.
    """
    need = 192 + 128
    if len(x) < need:
        return np.zeros(0, dtype=np.float32)

    y = x[192:]

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

    metric = (np.abs(corr) ** 2) / (e1 * e2 + 1e-12)
    return metric.astype(np.float32)


def find_peaks_from_metric(
    metric: np.ndarray,
    threshold: float,
    min_separation: int,
) -> list[tuple[int, float]]:
    idx = np.where(metric >= threshold)[0]

    if len(idx) == 0:
        return []

    groups: list[tuple[int, int]] = []
    start = int(idx[0])
    prev = int(idx[0])

    for v in idx[1:]:
        v = int(v)
        if v <= prev + 1:
            prev = v
        else:
            groups.append((start, prev))
            start = v
            prev = v

    groups.append((start, prev))

    peaks: list[tuple[int, float]] = []
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
    if not offsets:
        raise RuntimeError("No offsets available for phase estimation")

    phases = np.asarray([int(o) % period_samples for o in offsets], dtype=np.int64)
    bins = phases // bin_width_samples

    unique, counts = np.unique(bins, return_counts=True)
    best_bin = int(unique[np.argmax(counts)])

    mask = (bins == best_bin) | (bins == best_bin - 1) | (bins == best_bin + 1)
    cluster = phases[mask]

    if len(cluster) == 0:
        cluster = phases

    return int(np.median(cluster))


def refine_packet_start_ltf(
    iq: np.ndarray,
    expected_start: int,
    search_radius_samples: int,
) -> tuple[int, float]:
    a = max(0, int(expected_start) - int(search_radius_samples))
    b = min(len(iq), int(expected_start) + int(search_radius_samples) + 4000)

    block = np.asarray(iq[a:b], dtype=np.complex64)
    metric = ltf_metric_for_packet_starts(block)

    if len(metric) == 0:
        return int(expected_start), 0.0

    local = int(np.argmax(metric))
    val = float(metric[local])

    return a + local, val
