#!/usr/bin/env python3
"""
Legacy 802.11 OFDM receiver primitives for the controlled WiFi sensing beacon.

This module is intentionally matched to wifi_legacy_ofdm.py:
  - 20 MHz
  - legacy OFDM
  - 6 Mb/s BPSK 1/2
  - fixed scrambler initial state 0x5D
  - L-STF + L-LTF + L-SIG + DATA

A detected CSI estimate is provisional until the complete MPDU passes:
  - L-SIG validation
  - FCS validation
  - Beacon/BSSID validation
  - experiment Vendor IE validation
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, asdict
from typing import Any, Iterable

import numpy as np

try:
    from .wifi_legacy_ofdm import (
        ACTIVE_SUBCARRIERS,
        DATA_SUBCARRIERS,
        FFT_SIZE,
        PILOT_SUBCARRIERS,
        SAMPLE_RATE,
        generate_l_ltf,
        generate_l_stf,
        ltf_freq_sequence,
        sc_to_bin,
    )
except ImportError:
    from wifi_legacy_ofdm import (
        ACTIVE_SUBCARRIERS,
        DATA_SUBCARRIERS,
        FFT_SIZE,
        PILOT_SUBCARRIERS,
        SAMPLE_RATE,
        generate_l_ltf,
        generate_l_stf,
        ltf_freq_sequence,
        sc_to_bin,
    )


L_STF_SAMPLES = 160
L_LTF_SAMPLES = 160
L_SIG_SAMPLES = 80
PREAMBLE_AND_SIG_SAMPLES = L_STF_SAMPLES + L_LTF_SAMPLES + L_SIG_SAMPLES
OFDM_SYMBOL_SAMPLES = 80
CP_LEN = 16
N_DBPS_6M = 24
N_CBPS_6M = 48
EXPECTED_RATE_BITS_6M = [1, 1, 0, 1]


@dataclass
class DetectionCandidate:
    coarse_offset: int
    stf_metric: float
    coarse_cfo_hz: float


@dataclass
class SyncResult:
    packet_offset: int
    preamble_metric: float
    coarse_cfo_hz: float
    fine_cfo_hz: float
    total_cfo_hz: float


@dataclass
class SignalResult:
    valid: bool
    rate_bits: list[int]
    reserved: int
    length_bytes: int
    parity_ok: bool
    tail_ok: bool
    reason: str


@dataclass
class VendorIdentity:
    valid: bool
    oui: str | None
    vendor_type: int | None
    magic: str | None
    version: int | None
    transmitter_id: int | None
    experiment_id: int | None
    packet_counter: int | None
    reason: str


@dataclass
class MacResult:
    valid: bool
    fcs_valid: bool
    is_beacon: bool
    destination: str | None
    source: str | None
    bssid: str | None
    sequence_number: int | None
    beacon_interval_tu: int | None
    ssid: str | None
    vendor: VendorIdentity
    reason: str


@dataclass
class AcceptedBeacon:
    packet_offset: int
    stf_metric: float
    preamble_metric: float
    coarse_cfo_hz: float
    fine_cfo_hz: float
    total_cfo_hz: float
    rx_power_dbfs: float
    ltf_consistency_error: float
    signal: SignalResult
    mac: MacResult
    csi: np.ndarray

    def json_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("csi", None)
        out["csi_abs_mean"] = float(np.mean(np.abs(self.csi)))
        out["csi_abs_std"] = float(np.std(np.abs(self.csi)))
        out["csi_phase_std"] = float(np.std(np.angle(self.csi)))
        return out


def _moving_sum(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 0:
        raise ValueError("win must be positive")
    if len(x) < win:
        return np.zeros(0, dtype=x.dtype)
    c = np.concatenate([np.zeros(1, dtype=x.dtype), np.cumsum(x, dtype=x.dtype)])
    return c[win:] - c[:-win]


def stf_autocorrelation(
    iq: np.ndarray,
    lag: int = 16,
    win: int = 128,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Return normalized STF metric and complex delayed correlation.

    P[n] = sum conj(r[n+m]) r[n+m+lag]
    M[n] = |P[n]|^2 / (E1[n] E2[n] + eps)
    """
    x = np.asarray(iq, dtype=np.complex64)
    if len(x) < lag + win:
        return np.zeros(0, np.float32), np.zeros(0, np.complex64)

    a = x[:-lag]
    b = x[lag:]
    p = _moving_sum(np.conj(a) * b, win)
    e1 = _moving_sum((np.abs(a) ** 2).astype(np.float64), win)
    e2 = _moving_sum((np.abs(b) ** 2).astype(np.float64), win)
    m = (np.abs(p) ** 2) / (e1 * e2 + 1e-18)
    return m.astype(np.float32), p.astype(np.complex64)


def _threshold_regions(mask_indices: np.ndarray, max_gap: int = 1) -> list[tuple[int, int]]:
    if len(mask_indices) == 0:
        return []
    regions: list[tuple[int, int]] = []
    start = int(mask_indices[0])
    prev = start
    for value in mask_indices[1:]:
        value = int(value)
        if value <= prev + max_gap:
            prev = value
        else:
            regions.append((start, prev))
            start = value
            prev = value
    regions.append((start, prev))
    return regions


def detect_stf_candidates(
    iq: np.ndarray,
    sample_rate: float = SAMPLE_RATE,
    threshold: float = 0.72,
    min_plateau_samples: int = 48,
    min_separation_samples: int = 800,
    win: int = 128,
) -> list[DetectionCandidate]:
    metric, corr = stf_autocorrelation(iq, lag=16, win=win)
    idx = np.flatnonzero(metric >= threshold)
    regions = _threshold_regions(idx)

    candidates: list[DetectionCandidate] = []
    last_offset = -10**18

    for start, end in regions:
        if end - start + 1 < min_plateau_samples:
            continue

        # The plateau begins close to packet start. Use its first strong location.
        local_slice = metric[start:end + 1]
        peak_rel = int(np.argmax(local_slice))
        peak_idx = start + peak_rel
        coarse_offset = start

        if coarse_offset - last_offset < min_separation_samples:
            if candidates and metric[peak_idx] > candidates[-1].stf_metric:
                phase = float(np.angle(corr[peak_idx]))
                cfo = phase * sample_rate / (2.0 * np.pi * 16.0)
                candidates[-1] = DetectionCandidate(coarse_offset, float(metric[peak_idx]), float(cfo))
                last_offset = coarse_offset
            continue

        phase = float(np.angle(corr[peak_idx]))
        cfo = phase * sample_rate / (2.0 * np.pi * 16.0)
        candidates.append(
            DetectionCandidate(
                coarse_offset=int(coarse_offset),
                stf_metric=float(metric[peak_idx]),
                coarse_cfo_hz=float(cfo),
            )
        )
        last_offset = coarse_offset

    return candidates


def correct_cfo(iq: np.ndarray, cfo_hz: float, sample_rate: float = SAMPLE_RATE, n0: int = 0) -> np.ndarray:
    x = np.asarray(iq, dtype=np.complex64)
    n = np.arange(n0, n0 + len(x), dtype=np.float64)
    rot = np.exp(-1j * 2.0 * np.pi * float(cfo_hz) * n / float(sample_rate))
    return (x * rot).astype(np.complex64)


def known_preamble() -> np.ndarray:
    return np.concatenate([generate_l_stf(), generate_l_ltf()]).astype(np.complex64)


def normalized_template_metric(template: np.ndarray, segment: np.ndarray) -> float:
    et = float(np.vdot(template, template).real)
    es = float(np.vdot(segment, segment).real)
    if et <= 0.0 or es <= 0.0:
        return 0.0
    return float(np.abs(np.vdot(template, segment)) ** 2 / (et * es + 1e-18))


def estimate_coarse_cfo_at_packet(
    iq: np.ndarray,
    packet_offset: int,
    sample_rate: float = SAMPLE_RATE,
    lag: int = 16,
    win: int = 128,
) -> float:
    """Estimate coarse CFO from the actual L-STF at a refined packet start."""
    start = int(packet_offset)
    stop = start + lag + win
    if start < 0 or stop > len(iq):
        raise ValueError("Not enough L-STF samples for coarse CFO estimation")

    a = np.asarray(iq[start:start + win], dtype=np.complex64)
    b = np.asarray(iq[start + lag:start + lag + win], dtype=np.complex64)
    corr = np.vdot(a, b)
    phase = float(np.angle(corr))
    return float(phase * sample_rate / (2.0 * np.pi * lag))


def refine_packet_timing(
    iq: np.ndarray,
    candidate: DetectionCandidate,
    sample_rate: float = SAMPLE_RATE,
    search_before: int = 6500,
    search_after: int = 2000,
) -> tuple[int, float]:
    """
    Refine packet timing using several L-LTF repetition hypotheses.

    A lag-64 repetition metric alone is not unique: payload OFDM symbols and
    unrelated WiFi traffic can produce stronger peaks than the actual L-LTF.
    Therefore this routine:

      1. Computes the normalized lag-64 metric in a broad local window.
      2. Keeps several separated high-scoring hypotheses, not only the maximum.
      3. Estimates CFO independently for each hypothesis.
      4. Tests the known complete 160-sample L-LTF around each hypothesis.
      5. Selects the hypothesis with the highest coherent known-LTF metric.

    For a correct hypothesis:
        ltf1_start = packet_start + 192
        complete_ltf_start = packet_start + 160
    """
    x = np.asarray(iq, dtype=np.complex64)
    ltf = generate_l_ltf().astype(np.complex64)
    ltf_energy = float(np.vdot(ltf, ltf).real) + 1e-18

    n_start = max(
        192,
        int(candidate.coarse_offset) - int(search_before) + 192,
    )
    n_stop = min(
        len(x) - 128,
        int(candidate.coarse_offset) + int(search_after) + 192,
    )
    if n_stop < n_start:
        raise ValueError("Not enough samples for L-LTF repetition search")

    block = x[n_start:n_stop + 128]
    a = block[:-64]
    b = block[64:]

    corr = _moving_sum(np.conj(a) * b, 64)
    e1 = _moving_sum((np.abs(a) ** 2).astype(np.float64), 64)
    e2 = _moving_sum((np.abs(b) ** 2).astype(np.float64), 64)
    rep_metric = (np.abs(corr) ** 2) / (e1 * e2 + 1e-18)

    if len(rep_metric) == 0:
        raise ValueError("Empty L-LTF repetition metric")

    # Build local-max hypotheses. Keep them separated so one broad plateau does
    # not consume all hypotheses.
    order = np.argsort(rep_metric)[::-1]
    hypotheses: list[int] = []
    min_hypothesis_separation = 24
    max_hypotheses = 32

    for rel in order:
        rel = int(rel)
        if any(abs(rel - old) < min_hypothesis_separation for old in hypotheses):
            continue
        hypotheses.append(rel)
        if len(hypotheses) >= max_hypotheses:
            break

    best_packet_start = None
    best_template_metric = -1.0
    best_repetition_metric = -1.0

    for rel in hypotheses:
        ltf1_start = n_start + rel
        coarse_packet_start = ltf1_start - 192
        if coarse_packet_start < 0:
            continue

        s1 = x[ltf1_start:ltf1_start + 64]
        s2 = x[ltf1_start + 64:ltf1_start + 128]
        if len(s1) != 64 or len(s2) != 64:
            continue

        ltf_cfo = float(
            np.angle(np.vdot(s1, s2))
            * sample_rate
            / (2.0 * np.pi * 64.0)
        )

        # Resolve the remaining sample ambiguity around this hypothesis.
        local_start = max(0, coarse_packet_start - 20)
        local_stop = min(
            len(x) - (L_STF_SAMPLES + len(ltf)),
            coarse_packet_start + 20,
        )

        for packet_start in range(local_start, local_stop + 1):
            complete_ltf_start = packet_start + L_STF_SAMPLES
            segment = x[
                complete_ltf_start:
                complete_ltf_start + len(ltf)
            ]
            if len(segment) != len(ltf):
                continue

            corrected = correct_cfo(
                segment,
                ltf_cfo,
                sample_rate=sample_rate,
                n0=complete_ltf_start,
            )
            seg_energy = float(np.vdot(corrected, corrected).real) + 1e-18
            template_metric = float(
                np.abs(np.vdot(ltf, corrected)) ** 2
                / (ltf_energy * seg_energy + 1e-18)
            )

            if template_metric > best_template_metric:
                best_template_metric = template_metric
                best_repetition_metric = float(rep_metric[rel])
                best_packet_start = int(packet_start)

    if best_packet_start is None:
        raise ValueError("No valid L-LTF timing hypothesis")

    # The known-LTF metric is the decisive metric. The repetition score is kept
    # only as a weak floor for diagnostics.
    combined_metric = max(
        best_template_metric,
        0.25 * best_repetition_metric,
    )
    return best_packet_start, float(combined_metric)

def estimate_fine_cfo(
    coarse_corrected_packet: np.ndarray,
    sample_rate: float = SAMPLE_RATE,
) -> float:
    s1 = coarse_corrected_packet[192:256]
    s2 = coarse_corrected_packet[256:320]
    if len(s1) != 64 or len(s2) != 64:
        raise ValueError("Packet too short for L-LTF fine CFO")
    phase = float(np.angle(np.vdot(s1, s2)))
    return float(phase * sample_rate / (2.0 * np.pi * 64.0))


def synchronize_candidate(
    iq: np.ndarray,
    candidate: DetectionCandidate,
    sample_rate: float = SAMPLE_RATE,
    min_preamble_metric: float = 0.20,
) -> SyncResult:
    packet_offset, preamble_metric = refine_packet_timing(
        iq,
        candidate,
        sample_rate=sample_rate,
    )
    if preamble_metric < min_preamble_metric:
        raise ValueError(f"Preamble metric too low: {preamble_metric:.4f}")

    preamble = np.asarray(iq[packet_offset:packet_offset + 320], dtype=np.complex64)
    if len(preamble) < 320:
        raise ValueError("Truncated preamble")

    # Re-estimate coarse CFO from the actual refined L-STF. The STF detector
    # candidate may have originated elsewhere inside the same PPDU.
    coarse_cfo_hz = estimate_coarse_cfo_at_packet(
        iq,
        packet_offset,
        sample_rate=sample_rate,
    )
    coarse_corrected = correct_cfo(
        preamble,
        coarse_cfo_hz,
        sample_rate=sample_rate,
        n0=packet_offset,
    )
    fine_cfo = estimate_fine_cfo(coarse_corrected, sample_rate=sample_rate)

    return SyncResult(
        packet_offset=packet_offset,
        preamble_metric=preamble_metric,
        coarse_cfo_hz=coarse_cfo_hz,
        fine_cfo_hz=fine_cfo,
        total_cfo_hz=coarse_cfo_hz + fine_cfo,
    )


def _fft_ofdm_symbol(symbol80: np.ndarray) -> np.ndarray:
    if len(symbol80) != 80:
        raise ValueError("OFDM symbol must contain 80 samples")
    useful = symbol80[16:80]
    return np.fft.fft(useful, n=64) / 64.0 * np.sqrt(52.0)


def extract_ltf_csi(
    corrected_packet: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    if len(corrected_packet) < 320:
        raise ValueError("Packet too short for L-LTF")

    y1 = np.fft.fft(corrected_packet[192:256], n=64) / 64.0 * np.sqrt(52.0)
    y2 = np.fft.fft(corrected_packet[256:320], n=64) / 64.0 * np.sqrt(52.0)

    seq = ltf_freq_sequence()
    h1: list[complex] = []
    h2: list[complex] = []

    for sc in ACTIVE_SUBCARRIERS:
        x = seq[int(sc)]
        h1.append(y1[sc_to_bin(int(sc))] / x)
        h2.append(y2[sc_to_bin(int(sc))] / x)

    h1_arr = np.asarray(h1, np.complex64)
    h2_arr = np.asarray(h2, np.complex64)
    h = 0.5 * (h1_arr + h2_arr)

    consistency = float(
        np.sum(np.abs(h1_arr - h2_arr) ** 2)
        / (np.sum(np.abs(h1_arr) ** 2) + np.sum(np.abs(h2_arr) ** 2) + 1e-18)
    )
    return h, h1_arr, h2_arr, consistency


def _channel_vector_by_bin(csi: np.ndarray) -> np.ndarray:
    h = np.zeros(64, dtype=np.complex64)
    for sc, value in zip(ACTIVE_SUBCARRIERS, csi):
        h[sc_to_bin(int(sc))] = value
    return h


def equalize_symbol(symbol80: np.ndarray, csi: np.ndarray) -> np.ndarray:
    y = _fft_ofdm_symbol(symbol80)
    h = _channel_vector_by_bin(csi)
    out = np.zeros(64, dtype=np.complex64)
    active_bins = [sc_to_bin(int(sc)) for sc in ACTIVE_SUBCARRIERS]
    for idx in active_bins:
        if abs(h[idx]) > 1e-10:
            out[idx] = y[idx] / h[idx]
    return out


def correct_common_phase_from_pilots(equalized: np.ndarray) -> np.ndarray:
    expected = {-21: 1.0, -7: 1.0, 7: 1.0, 21: -1.0}
    acc = 0j
    for sc, ref in expected.items():
        acc += equalized[sc_to_bin(sc)] * np.conj(ref)
    phase = float(np.angle(acc)) if abs(acc) > 0 else 0.0
    return equalized * np.exp(-1j * phase)


def hard_bpsk_bits(equalized: np.ndarray, subcarriers: Iterable[int]) -> list[int]:
    return [0 if equalized[sc_to_bin(int(sc))].real >= 0.0 else 1 for sc in subcarriers]


def deinterleave_48(interleaved: list[int]) -> list[int]:
    if len(interleaved) != 48:
        raise ValueError("Expected 48 interleaved bits")
    original = [0] * 48
    for k in range(48):
        i = 3 * (k % 16) + (k // 16)
        original[k] = int(interleaved[i])
    return original


def _parity(value: int) -> int:
    return value.bit_count() & 1


def viterbi_decode_rate_half(
    coded_bits: list[int],
    force_final_state_zero: bool = False,
) -> list[int]:
    """
    Hard-decision Viterbi decoder matched to convolutional_encode().

    Encoder state is a 7-bit shift register:
        new_state = ((old_state << 1) | bit) & 0x7f
        outputs from generators 0133 and 0171.
    """
    if len(coded_bits) % 2:
        raise ValueError("Rate-1/2 coded bit count must be even")

    g0 = 0o133
    g1 = 0o171
    n_steps = len(coded_bits) // 2
    inf = 10**9

    metrics = np.full(128, inf, dtype=np.int32)
    metrics[0] = 0
    prev_state = np.full((n_steps, 128), -1, dtype=np.int16)
    prev_bit = np.full((n_steps, 128), -1, dtype=np.int8)

    for t in range(n_steps):
        r0 = int(coded_bits[2 * t])
        r1 = int(coded_bits[2 * t + 1])
        new_metrics = np.full(128, inf, dtype=np.int32)

        for state in range(128):
            base = int(metrics[state])
            if base >= inf:
                continue
            for bit in (0, 1):
                ns = ((state << 1) | bit) & 0x7F
                o0 = _parity(ns & g0)
                o1 = _parity(ns & g1)
                distance = (o0 != r0) + (o1 != r1)
                score = base + distance
                if score < new_metrics[ns]:
                    new_metrics[ns] = score
                    prev_state[t, ns] = state
                    prev_bit[t, ns] = bit

        metrics = new_metrics

    state = 0 if force_final_state_zero else int(np.argmin(metrics))
    decoded = [0] * n_steps

    for t in range(n_steps - 1, -1, -1):
        bit = int(prev_bit[t, state])
        ps = int(prev_state[t, state])
        if bit < 0 or ps < 0:
            raise ValueError("Viterbi traceback failed")
        decoded[t] = bit
        state = ps

    return decoded


def decode_signal_symbol(
    corrected_packet: np.ndarray,
    csi: np.ndarray,
) -> SignalResult:
    if len(corrected_packet) < 400:
        return SignalResult(False, [], 0, 0, False, False, "Truncated L-SIG")

    eq = equalize_symbol(corrected_packet[320:400], csi)
    eq = correct_common_phase_from_pilots(eq)
    interleaved = hard_bpsk_bits(eq, DATA_SUBCARRIERS)
    coded = deinterleave_48(interleaved)
    bits = viterbi_decode_rate_half(coded, force_final_state_zero=True)

    if len(bits) != 24:
        return SignalResult(False, [], 0, 0, False, False, "Bad decoded L-SIG length")

    rate = bits[0:4]
    reserved = bits[4]
    length = sum(int(bits[5 + i]) << i for i in range(12))
    parity_ok = (sum(bits[:17]) & 1) == bits[17]
    tail_ok = all(bit == 0 for bit in bits[18:24])

    reasons = []
    if rate != EXPECTED_RATE_BITS_6M:
        reasons.append(f"Unsupported RATE bits {rate}")
    if reserved != 0:
        reasons.append("Reserved bit is nonzero")
    if not parity_ok:
        reasons.append("Parity failed")
    if not tail_ok:
        reasons.append("Tail bits are nonzero")
    if length <= 0 or length > 4095:
        reasons.append(f"Invalid PSDU length {length}")

    valid = not reasons
    return SignalResult(
        valid=valid,
        rate_bits=[int(x) for x in rate],
        reserved=int(reserved),
        length_bytes=int(length),
        parity_ok=bool(parity_ok),
        tail_ok=bool(tail_ok),
        reason="OK" if valid else "; ".join(reasons),
    )


def descramble_bits(scrambled: list[int], initial_state: int = 0x5D) -> list[int]:
    if not 1 <= initial_state <= 0x7F:
        raise ValueError("Scrambler initial state must be nonzero")
    state = [(initial_state >> i) & 1 for i in range(7)]
    out: list[int] = []
    for bit in scrambled:
        feedback = state[6] ^ state[3]
        out.append(int(bit) ^ feedback)
        state = [feedback] + state[:6]
    return out


def bits_to_bytes_lsb_first(bits: list[int]) -> bytes:
    if len(bits) % 8:
        raise ValueError("Bit count must be a multiple of 8")
    out = bytearray()
    for i in range(0, len(bits), 8):
        value = 0
        for j, bit in enumerate(bits[i:i + 8]):
            value |= (int(bit) & 1) << j
        out.append(value)
    return bytes(out)


def data_symbol_count(psdu_length_bytes: int) -> int:
    return int(math.ceil((16 + 8 * psdu_length_bytes + 6) / N_DBPS_6M))


def decode_data_psdu(
    corrected_packet: np.ndarray,
    csi: np.ndarray,
    psdu_length_bytes: int,
    scrambler_state: int = 0x5D,
) -> bytes:
    n_sym = data_symbol_count(psdu_length_bytes)
    required = PREAMBLE_AND_SIG_SAMPLES + n_sym * OFDM_SYMBOL_SAMPLES
    if len(corrected_packet) < required:
        raise ValueError(f"Truncated DATA: need {required}, have {len(corrected_packet)}")

    all_coded: list[int] = []
    data_start = PREAMBLE_AND_SIG_SAMPLES

    for symbol_index in range(n_sym):
        start = data_start + symbol_index * OFDM_SYMBOL_SAMPLES
        eq = equalize_symbol(corrected_packet[start:start + 80], csi)
        eq = correct_common_phase_from_pilots(eq)
        interleaved = hard_bpsk_bits(eq, DATA_SUBCARRIERS)
        all_coded.extend(deinterleave_48(interleaved))

    decoded_scrambled = viterbi_decode_rate_half(all_coded, force_final_state_zero=False)
    decoded = descramble_bits(decoded_scrambled, initial_state=scrambler_state)

    service = decoded[:16]
    if len(service) != 16:
        raise ValueError("Missing SERVICE field")

    psdu_bits = decoded[16:16 + 8 * psdu_length_bytes]
    if len(psdu_bits) != 8 * psdu_length_bytes:
        raise ValueError("Decoded PSDU has wrong length")

    return bits_to_bytes_lsb_first(psdu_bits)


def mac_to_string(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def parse_information_elements(body: bytes, start: int = 12) -> list[tuple[int, bytes]]:
    ies: list[tuple[int, bytes]] = []
    pos = start
    while pos + 2 <= len(body):
        element_id = body[pos]
        length = body[pos + 1]
        pos += 2
        if pos + length > len(body):
            break
        ies.append((element_id, body[pos:pos + length]))
        pos += length
    return ies


def parse_vendor_identity(
    ies: list[tuple[int, bytes]],
    expected_oui: bytes,
    expected_vendor_type: int,
    expected_magic: bytes,
    expected_version: int,
    expected_transmitter_id: int,
    expected_experiment_id: int,
) -> VendorIdentity:
    expected_magic8 = expected_magic[:8].ljust(8, b"\x00")

    for element_id, payload in ies:
        if element_id != 221 or len(payload) < 21:
            continue
        if payload[:3] != expected_oui:
            continue
        if payload[3] != expected_vendor_type:
            continue

        magic = payload[4:12]
        version = payload[12]
        transmitter_id = int.from_bytes(payload[13:15], "big")
        experiment_id = int.from_bytes(payload[15:17], "big")
        counter = int.from_bytes(payload[17:21], "big")

        reasons = []
        if magic != expected_magic8:
            reasons.append("Magic mismatch")
        if version != expected_version:
            reasons.append("Version mismatch")
        if transmitter_id != expected_transmitter_id:
            reasons.append("Transmitter ID mismatch")
        if experiment_id != expected_experiment_id:
            reasons.append("Experiment ID mismatch")

        valid = not reasons
        return VendorIdentity(
            valid=valid,
            oui=mac_to_string(payload[:3]),
            vendor_type=int(payload[3]),
            magic=magic.rstrip(b"\x00").decode("ascii", errors="replace"),
            version=int(version),
            transmitter_id=int(transmitter_id),
            experiment_id=int(experiment_id),
            packet_counter=int(counter),
            reason="OK" if valid else "; ".join(reasons),
        )

    return VendorIdentity(
        valid=False,
        oui=None,
        vendor_type=None,
        magic=None,
        version=None,
        transmitter_id=None,
        experiment_id=None,
        packet_counter=None,
        reason="Expected Vendor IE not found",
    )


def parse_and_validate_beacon(
    mpdu: bytes,
    expected_bssid: str,
    expected_ssid: str,
    expected_oui: bytes,
    expected_vendor_type: int,
    expected_magic: bytes,
    expected_version: int,
    expected_transmitter_id: int,
    expected_experiment_id: int,
) -> MacResult:
    empty_vendor = VendorIdentity(False, None, None, None, None, None, None, None, "Not parsed")

    if len(mpdu) < 24 + 12 + 4:
        return MacResult(False, False, False, None, None, None, None, None, None, empty_vendor, "MPDU too short")

    stored_fcs = mpdu[-4:]
    calculated_fcs = struct.pack("<I", zlib.crc32(mpdu[:-4]) & 0xFFFFFFFF)
    fcs_valid = stored_fcs == calculated_fcs

    frame_control = int.from_bytes(mpdu[0:2], "little")
    frame_type = (frame_control >> 2) & 0x3
    subtype = (frame_control >> 4) & 0xF
    is_beacon = frame_type == 0 and subtype == 8

    destination = mac_to_string(mpdu[4:10])
    source = mac_to_string(mpdu[10:16])
    bssid = mac_to_string(mpdu[16:22])
    seq_ctrl = int.from_bytes(mpdu[22:24], "little")
    sequence_number = (seq_ctrl >> 4) & 0xFFF

    body = mpdu[24:-4]
    if len(body) < 12:
        return MacResult(False, fcs_valid, is_beacon, destination, source, bssid, sequence_number, None, None, empty_vendor, "Beacon body too short")

    beacon_interval_tu = int.from_bytes(body[8:10], "little")
    ies = parse_information_elements(body, start=12)

    ssid = None
    for element_id, payload in ies:
        if element_id == 0:
            ssid = payload.decode("utf-8", errors="replace")
            break

    vendor = parse_vendor_identity(
        ies=ies,
        expected_oui=expected_oui,
        expected_vendor_type=expected_vendor_type,
        expected_magic=expected_magic,
        expected_version=expected_version,
        expected_transmitter_id=expected_transmitter_id,
        expected_experiment_id=expected_experiment_id,
    )

    reasons = []
    if not fcs_valid:
        reasons.append("FCS failed")
    if not is_beacon:
        reasons.append("Not a Beacon frame")
    if destination != "ff:ff:ff:ff:ff:ff":
        reasons.append("Destination is not broadcast")
    if source.lower() != expected_bssid.lower():
        reasons.append("Source MAC mismatch")
    if bssid.lower() != expected_bssid.lower():
        reasons.append("BSSID mismatch")
    if ssid != expected_ssid:
        reasons.append("SSID mismatch")
    if not vendor.valid:
        reasons.append(vendor.reason)

    valid = not reasons
    return MacResult(
        valid=valid,
        fcs_valid=fcs_valid,
        is_beacon=is_beacon,
        destination=destination,
        source=source,
        bssid=bssid,
        sequence_number=sequence_number,
        beacon_interval_tu=beacon_interval_tu,
        ssid=ssid,
        vendor=vendor,
        reason="OK" if valid else "; ".join(reasons),
    )


def decode_candidate(
    iq: np.ndarray,
    candidate: DetectionCandidate,
    *,
    sample_rate: float = SAMPLE_RATE,
    min_preamble_metric: float = 0.20,
    max_ltf_consistency_error: float = 0.20,
    expected_bssid: str = "02:11:22:33:44:55",
    expected_ssid: str = "SENSING_WIFI",
    expected_oui: bytes = b"\x02\x11\x22",
    expected_vendor_type: int = 1,
    expected_magic: bytes = b"ALBSENS",
    expected_version: int = 1,
    expected_transmitter_id: int = 1,
    expected_experiment_id: int = 1,
) -> AcceptedBeacon:
    sync = synchronize_candidate(
        iq,
        candidate,
        sample_rate=sample_rate,
        min_preamble_metric=min_preamble_metric,
    )

    available = np.asarray(iq[sync.packet_offset:], dtype=np.complex64)
    if len(available) < 400:
        raise ValueError("Candidate is truncated before L-SIG")

    corrected = correct_cfo(
        available,
        sync.total_cfo_hz,
        sample_rate=sample_rate,
        n0=sync.packet_offset,
    )

    csi, _h1, _h2, consistency = extract_ltf_csi(corrected)
    if consistency > max_ltf_consistency_error:
        raise ValueError(
            f"L-LTF consistency error too high: {consistency:.4f} > {max_ltf_consistency_error:.4f}"
        )

    signal = decode_signal_symbol(corrected, csi)
    if not signal.valid:
        raise ValueError(f"L-SIG invalid: {signal.reason}")

    n_sym = data_symbol_count(signal.length_bytes)
    packet_samples = PREAMBLE_AND_SIG_SAMPLES + n_sym * OFDM_SYMBOL_SAMPLES
    if len(corrected) < packet_samples:
        raise ValueError(
            f"Truncated PPDU: requires {packet_samples} samples, only {len(corrected)} available"
        )

    corrected_packet = corrected[:packet_samples]
    psdu = decode_data_psdu(
        corrected_packet,
        csi,
        signal.length_bytes,
        scrambler_state=0x5D,
    )

    mac = parse_and_validate_beacon(
        psdu,
        expected_bssid=expected_bssid,
        expected_ssid=expected_ssid,
        expected_oui=expected_oui,
        expected_vendor_type=expected_vendor_type,
        expected_magic=expected_magic,
        expected_version=expected_version,
        expected_transmitter_id=expected_transmitter_id,
        expected_experiment_id=expected_experiment_id,
    )
    if not mac.valid:
        raise ValueError(f"MAC identity validation failed: {mac.reason}")

    rx_power = float(
        10.0 * np.log10(np.mean(np.abs(available[:320]) ** 2) + 1e-18)
    )

    return AcceptedBeacon(
        packet_offset=sync.packet_offset,
        stf_metric=candidate.stf_metric,
        preamble_metric=sync.preamble_metric,
        coarse_cfo_hz=sync.coarse_cfo_hz,
        fine_cfo_hz=sync.fine_cfo_hz,
        total_cfo_hz=sync.total_cfo_hz,
        rx_power_dbfs=rx_power,
        ltf_consistency_error=consistency,
        signal=signal,
        mac=mac,
        csi=csi,
    )
