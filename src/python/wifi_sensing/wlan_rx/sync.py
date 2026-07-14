from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .common import (
    L_STF_SAMPLES,
    SAMPLE_RATE,
    correct_cfo,
    moving_sum,
    normalized_correlation,
)
from .detection import StfCandidate
from .sequences import l_ltf_time


@dataclass
class SyncResult:
    packet_offset: int
    coarse_cfo_hz: float
    fine_cfo_hz: float
    total_cfo_hz: float
    ltf_template_metric: float
    ltf_repeat_metric: float


def _repeat64_hypotheses(
    iq: np.ndarray,
    center: int,
    search_before: int,
    search_after: int,
    max_hypotheses: int = 48,
) -> list[tuple[int, float]]:
    x = np.asarray(iq, dtype=np.complex64)
    start = max(0, center - search_before)
    stop = min(len(x) - 128, center + search_after)
    if stop <= start:
        return []

    block = x[start:stop + 128]
    a = block[:-64]
    b = block[64:]
    corr = moving_sum(np.conj(a) * b, 64)
    e1 = moving_sum((np.abs(a) ** 2).astype(np.float64), 64)
    e2 = moving_sum((np.abs(b) ** 2).astype(np.float64), 64)
    metric = (np.abs(corr) ** 2) / (e1 * e2 + 1e-18)

    order = np.argsort(metric)[::-1]
    chosen: list[tuple[int, float]] = []
    for rel in order:
        pos = start + int(rel)
        if any(abs(pos - old_pos) < 24 for old_pos, _ in chosen):
            continue
        chosen.append((pos, float(metric[int(rel)])))
        if len(chosen) >= max_hypotheses:
            break
    return chosen


def synchronize(
    iq: np.ndarray,
    candidate: StfCandidate,
    *,
    sample_rate: float = SAMPLE_RATE,
    search_before: int = 7000,
    search_after: int = 2500,
    min_template_metric: float = 0.08,
) -> SyncResult:
    """
    Find exact packet start using the known L-LTF.

    Each lag-64 hypothesis is treated as a possible start of LTF1. For each
    hypothesis:
      packet_start = ltf1_start - 192
    Then estimate CFO from LTF1/LTF2 and test the complete known L-LTF.
    """
    x = np.asarray(iq, dtype=np.complex64)
    ltf = l_ltf_time()

    # Expected LTF1 is roughly candidate+192, but candidate can be late.
    center = int(candidate.coarse_offset) + 192
    hypotheses = _repeat64_hypotheses(
        x,
        center=center,
        search_before=search_before,
        search_after=search_after,
    )
    if not hypotheses:
        raise ValueError("No L-LTF hypotheses")

    best = None
    best_score = -1.0

    for ltf1_start, repeat_metric in hypotheses:
        packet_guess = ltf1_start - 192
        if packet_guess < 0:
            continue

        s1 = x[ltf1_start:ltf1_start + 64]
        s2 = x[ltf1_start + 64:ltf1_start + 128]
        if len(s1) != 64 or len(s2) != 64:
            continue

        fine_from_pair = float(
            np.angle(np.vdot(s1, s2)) * sample_rate / (2.0 * np.pi * 64.0)
        )

        for packet_start in range(max(0, packet_guess - 24), packet_guess + 25):
            complete_ltf_start = packet_start + L_STF_SAMPLES
            seg = x[complete_ltf_start:complete_ltf_start + len(ltf)]
            if len(seg) != len(ltf):
                continue

            corrected = correct_cfo(
                seg,
                fine_from_pair,
                sample_rate=sample_rate,
                start_index=complete_ltf_start,
            )
            template_metric = normalized_correlation(ltf, corrected)

            # Prefer the exact known L-LTF; repeat metric is only auxiliary.
            score = template_metric + 0.05 * repeat_metric
            if score > best_score:
                best_score = score
                best = (
                    packet_start,
                    fine_from_pair,
                    template_metric,
                    repeat_metric,
                )

    if best is None:
        raise ValueError("No valid L-LTF timing hypothesis")

    packet_start, pair_cfo, template_metric, repeat_metric = best
    if template_metric < min_template_metric:
        raise ValueError(f"L-LTF template metric too low: {template_metric:.4f}")

    # Recompute coarse CFO from the actual STF.
    a = x[packet_start:packet_start + 128]
    b = x[packet_start + 16:packet_start + 144]
    if len(a) != 128 or len(b) != 128:
        raise ValueError("Truncated L-STF")
    coarse_cfo = float(
        np.angle(np.vdot(a, b)) * sample_rate / (2.0 * np.pi * 16.0)
    )

    preamble = x[packet_start:packet_start + 320]
    coarse_corrected = correct_cfo(
        preamble,
        coarse_cfo,
        sample_rate=sample_rate,
        start_index=packet_start,
    )
    s1c = coarse_corrected[192:256]
    s2c = coarse_corrected[256:320]
    fine_cfo = float(
        np.angle(np.vdot(s1c, s2c)) * sample_rate / (2.0 * np.pi * 64.0)
    )

    return SyncResult(
        packet_offset=int(packet_start),
        coarse_cfo_hz=coarse_cfo,
        fine_cfo_hz=fine_cfo,
        total_cfo_hz=coarse_cfo + fine_cfo,
        ltf_template_metric=float(template_metric),
        ltf_repeat_metric=float(repeat_metric),
    )
