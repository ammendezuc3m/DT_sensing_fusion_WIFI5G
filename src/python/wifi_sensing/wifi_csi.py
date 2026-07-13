#!/usr/bin/env python3
"""
Fast WiFi legacy OFDM packet detection and CSI extraction.

Detection:
  - Uses L-STF 16-sample periodicity for fast online detection.
  - Refines timing locally using the known L-STF + L-LTF preamble.

CSI:
  - Extracted from the two L-LTF OFDM symbols.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np

try:
    from .wifi_legacy_ofdm import (
        SAMPLE_RATE,
        ACTIVE_SUBCARRIERS,
        FFT_SIZE,
        generate_l_stf,
        generate_l_ltf,
        ltf_freq_sequence,
        sc_to_bin,
    )
except ImportError:
    from wifi_legacy_ofdm import (
        SAMPLE_RATE,
        ACTIVE_SUBCARRIERS,
        FFT_SIZE,
        generate_l_stf,
        generate_l_ltf,
        ltf_freq_sequence,
        sc_to_bin,
    )


@dataclass
class CsiResult:
    offset: int
    metric: float
    cfo_hz: float
    csi: np.ndarray
    rx_power_db: float


def known_preamble() -> np.ndarray:
    return np.concatenate([generate_l_stf(), generate_l_ltf()]).astype(np.complex64)


def moving_sum(x: np.ndarray, win: int) -> np.ndarray:
    if len(x) < win:
        return np.zeros(0, dtype=x.dtype)
    return np.convolve(x, np.ones(win, dtype=x.dtype), mode="valid")


def stf_autocorr_metric(iq: np.ndarray, lag: int = 16, win: int = 144) -> np.ndarray:
    """
    Fast Schmidl-style metric based on L-STF repetition.

    metric[n] = |sum conj(x[n+k]) x[n+k+16]|^2 / energy^2
    """
    if len(iq) < lag + win + 1:
        return np.zeros(0, dtype=np.float32)

    x1 = iq[:-lag]
    x2 = iq[lag:]

    corr_terms = np.conj(x1) * x2
    energy_terms = np.abs(x2) ** 2

    p = moving_sum(corr_terms.astype(np.complex64), win)
    r = moving_sum(energy_terms.astype(np.float32), win)

    metric = (np.abs(p) ** 2) / (r ** 2 + 1e-12)
    return metric.astype(np.float32)


def local_preamble_refine(iq: np.ndarray, coarse: int, search_before: int = 80, search_after: int = 160) -> tuple[int, float]:
    """
    Refine packet start by correlating locally with known L-STF+L-LTF.
    """
    tpl = known_preamble()
    tpl_energy = float(np.sum(np.abs(tpl) ** 2)) + 1e-12

    start = max(0, coarse - search_before)
    stop = min(len(iq) - len(tpl), coarse + search_after)

    if stop <= start:
        return coarse, 0.0

    best_i = start
    best_m = -1.0

    for i in range(start, stop + 1):
        seg = iq[i:i + len(tpl)]
        seg_energy = float(np.sum(np.abs(seg) ** 2)) + 1e-12
        c = np.abs(np.vdot(tpl, seg)) ** 2
        m = float(c / (tpl_energy * seg_energy))

        if m > best_m:
            best_m = m
            best_i = i

    return int(best_i), float(best_m)


def find_packet_offsets(
    iq: np.ndarray,
    min_metric: float = 0.35,
    min_separation_samples: int = 1000,
    max_packets: int | None = None,
) -> list[tuple[int, float]]:

    metric = stf_autocorr_metric(iq)

    if len(metric) == 0:
        return []

    candidates = np.where(metric >= min_metric)[0]

    if len(candidates) == 0:
        return []

    selected: list[tuple[int, float]] = []
    last_selected = -10**12

    # Group contiguous threshold crossings.
    groups: list[tuple[int, int]] = []
    g_start = int(candidates[0])
    prev = int(candidates[0])

    for c in candidates[1:]:
        c = int(c)
        if c <= prev + 1:
            prev = c
        else:
            groups.append((g_start, prev))
            g_start = c
            prev = c

    groups.append((g_start, prev))

    for start, end in groups:
        if start - last_selected < min_separation_samples:
            continue

        # Use the beginning of the plateau as coarse start, then refine.
        refined, refine_metric = local_preamble_refine(iq, start)

        if refine_metric < min_metric:
            continue

        if refined - last_selected < min_separation_samples:
            continue

        selected.append((refined, refine_metric))
        last_selected = refined

        if max_packets is not None and len(selected) >= max_packets:
            break

    return selected


def estimate_cfo_from_ltf(packet: np.ndarray, sample_rate: float = SAMPLE_RATE) -> float:
    ltf_start = 160
    s1_start = ltf_start + 32
    s2_start = s1_start + 64

    if len(packet) < s2_start + 64:
        raise ValueError("Packet too short for L-LTF CFO estimation")

    s1 = packet[s1_start:s1_start + 64]
    s2 = packet[s2_start:s2_start + 64]

    phase = np.angle(np.vdot(s1, s2))
    cfo_hz = phase / (2 * np.pi * 64 / sample_rate)

    return float(cfo_hz)


def correct_cfo(iq: np.ndarray, cfo_hz: float, sample_rate: float = SAMPLE_RATE) -> np.ndarray:
    n = np.arange(len(iq), dtype=np.float64)
    rot = np.exp(-1j * 2 * np.pi * cfo_hz * n / sample_rate)
    return (iq * rot).astype(np.complex64)


def extract_csi_from_packet(packet: np.ndarray, sample_rate: float = SAMPLE_RATE) -> tuple[np.ndarray, float]:
    cfo_hz = estimate_cfo_from_ltf(packet, sample_rate=sample_rate)
    pkt = correct_cfo(packet, cfo_hz, sample_rate=sample_rate)

    ltf_start = 160
    s1_start = ltf_start + 32
    s2_start = s1_start + 64

    s1 = pkt[s1_start:s1_start + 64]
    s2 = pkt[s2_start:s2_start + 64]

    y1 = np.fft.fft(s1, n=FFT_SIZE) / FFT_SIZE * np.sqrt(52)
    y2 = np.fft.fft(s2, n=FFT_SIZE) / FFT_SIZE * np.sqrt(52)

    y = 0.5 * (y1 + y2)

    ltf_seq = ltf_freq_sequence()
    csi = []

    for sc in ACTIVE_SUBCARRIERS:
        x = ltf_seq[int(sc)]
        csi.append(y[sc_to_bin(int(sc))] / x)

    return np.asarray(csi, dtype=np.complex64), cfo_hz


def extract_all_csi(
    iq: np.ndarray,
    sample_rate: float = SAMPLE_RATE,
    min_metric: float = 0.35,
    min_separation_samples: int = 1000,
) -> list[CsiResult]:

    offsets = find_packet_offsets(
        iq,
        min_metric=min_metric,
        min_separation_samples=min_separation_samples,
    )

    results: list[CsiResult] = []

    min_packet_len = 160 + 160

    for off, metric in offsets:
        if off + min_packet_len > len(iq):
            continue

        packet = iq[off:off + min_packet_len + 1600]

        try:
            csi, cfo_hz = extract_csi_from_packet(packet, sample_rate=sample_rate)
        except Exception:
            continue

        rx_power = float(np.mean(np.abs(packet[:min_packet_len]) ** 2) + 1e-12)
        rx_power_db = 10 * np.log10(rx_power)

        results.append(CsiResult(
            offset=int(off),
            metric=float(metric),
            cfo_hz=float(cfo_hz),
            csi=csi,
            rx_power_db=float(rx_power_db),
        ))

    return results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input-npz", required=True)
    p.add_argument("--min-metric", type=float, default=0.35)
    args = p.parse_args()

    d = np.load(args.input_npz)
    iq = d["waveform"].astype(np.complex64)

    results = extract_all_csi(iq, min_metric=args.min_metric)

    print(f"Detected packets: {len(results)}")

    for r in results[:5]:
        print(f"offset={r.offset} metric={r.metric:.3f} cfo_hz={r.cfo_hz:.2f} csi_shape={r.csi.shape}")


if __name__ == "__main__":
    main()
