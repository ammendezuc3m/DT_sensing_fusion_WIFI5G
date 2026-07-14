from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .common import SAMPLE_RATE, moving_sum


@dataclass
class StfCandidate:
    coarse_offset: int
    metric: float
    coarse_cfo_hz: float
    plateau_start: int
    plateau_end: int


def stf_metric(
    iq: np.ndarray,
    lag: int = 16,
    win: int = 128,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(iq, dtype=np.complex64)
    if len(x) < lag + win:
        return np.zeros(0, np.float32), np.zeros(0, np.complex64)

    a = x[:-lag]
    b = x[lag:]
    corr = moving_sum(np.conj(a) * b, win)
    e1 = moving_sum((np.abs(a) ** 2).astype(np.float64), win)
    e2 = moving_sum((np.abs(b) ** 2).astype(np.float64), win)
    metric = (np.abs(corr) ** 2) / (e1 * e2 + 1e-18)
    return metric.astype(np.float32), corr.astype(np.complex64)


def _regions(indices: np.ndarray, max_gap: int = 2) -> list[tuple[int, int]]:
    if len(indices) == 0:
        return []
    out = []
    start = prev = int(indices[0])
    for value in indices[1:]:
        value = int(value)
        if value <= prev + max_gap:
            prev = value
        else:
            out.append((start, prev))
            start = prev = value
    out.append((start, prev))
    return out


def detect_stf(
    iq: np.ndarray,
    *,
    sample_rate: float = SAMPLE_RATE,
    threshold: float = 0.65,
    min_plateau: int = 48,
    min_separation: int = 4800,
) -> list[StfCandidate]:
    metric, corr = stf_metric(iq)
    idx = np.flatnonzero(metric >= threshold)
    regions = _regions(idx)

    raw: list[StfCandidate] = []
    for start, end in regions:
        if end - start + 1 < min_plateau:
            continue
        local = start + int(np.argmax(metric[start:end + 1]))
        phase = float(np.angle(corr[local]))
        cfo = phase * sample_rate / (2.0 * np.pi * 16.0)
        raw.append(
            StfCandidate(
                coarse_offset=int(start),
                metric=float(metric[local]),
                coarse_cfo_hz=float(cfo),
                plateau_start=int(start),
                plateau_end=int(end),
            )
        )

    selected: list[StfCandidate] = []
    for cand in raw:
        if selected and cand.coarse_offset - selected[-1].coarse_offset < min_separation:
            if cand.metric > selected[-1].metric:
                selected[-1] = cand
            continue
        selected.append(cand)
    return selected
