#!/usr/bin/env python3
"""
Frequency offset utilities for the Python SSB pipeline.

The current estimator uses cyclic-prefix correlation after coarse PSS timing.

For an OFDM symbol:
    CP repeats the last CP samples of the useful OFDM symbol.
    The phase rotation between CP and the repeated tail is caused by CFO.

Estimated CFO:
    angle(sum(tail * conj(cp))) * Fs / (2*pi*NFFT)

Correction:
    waveform_corrected[n] = waveform[n] * exp(j * sign * 2*pi*cfo_hz*n/Fs)

Use sign=-1 for the same convention as the MATLAB pipeline used with
FrequencyCorrectionSign = -1.
"""

from __future__ import annotations

import numpy as np


def cp_lengths_for_30khz(num_symbols: int) -> list[int]:
    return [40 if i == 0 else 36 for i in range(num_symbols)]


def apply_frequency_correction(
    waveform: np.ndarray,
    cfo_hz: float,
    sample_rate: float,
    sign: float = -1.0,
) -> np.ndarray:
    n = np.arange(len(waveform), dtype=np.float64)
    rot = np.exp(1j * sign * 2.0 * np.pi * cfo_hz * n / sample_rate)
    return (waveform * rot).astype(np.complex64)


def estimate_cfo_cp_aligned(
    waveform_aligned: np.ndarray,
    sample_rate: float,
    nfft: int = 512,
    num_symbols: int = 6,
) -> dict:
    cp_lengths = cp_lengths_for_30khz(num_symbols)

    pos = 0
    estimates_hz = []
    weights = []

    for sym_idx, cp_len in enumerate(cp_lengths):
        sym_start = pos
        body_start = sym_start + cp_len
        body_end = body_start + nfft

        if body_end > len(waveform_aligned):
            break

        cp = waveform_aligned[sym_start : sym_start + cp_len]
        tail = waveform_aligned[body_end - cp_len : body_end]

        corr = np.sum(tail * np.conj(cp))
        weight = float(np.abs(corr))

        if weight > 0:
            cfo_hz = float(np.angle(corr) * sample_rate / (2.0 * np.pi * nfft))
            estimates_hz.append(cfo_hz)
            weights.append(weight)

        pos = body_end

    if not estimates_hz:
        return {
            "ok": False,
            "num_estimates": 0,
            "median_hz": None,
            "mean_hz": None,
            "weighted_mean_hz": None,
            "std_hz": None,
            "per_symbol_hz": [],
        }

    est = np.asarray(estimates_hz, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)

    return {
        "ok": True,
        "num_estimates": int(len(est)),
        "median_hz": float(np.median(est)),
        "mean_hz": float(np.mean(est)),
        "weighted_mean_hz": float(np.sum(est * w) / np.sum(w)),
        "std_hz": float(np.std(est)),
        "per_symbol_hz": [float(x) for x in est],
    }
