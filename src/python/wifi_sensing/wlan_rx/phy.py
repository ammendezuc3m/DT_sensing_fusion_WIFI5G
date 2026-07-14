from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .coding import (
    bits_to_bytes_lsb,
    deinterleave_bpsk_48,
    descramble,
    viterbi_decode_rate_half,
)
from .common import (
    ACTIVE_SUBCARRIERS,
    DATA_SUBCARRIERS,
    FFT_SIZE,
    OFDM_SYMBOL_SAMPLES,
    PREAMBLE_AND_SIG_SAMPLES,
    SAMPLE_RATE,
    sc_to_bin,
)
from .sequences import l_ltf_freq_map
from ..wifi_legacy_ofdm import pilot_values


@dataclass
class SignalInfo:
    valid: bool
    rate_bits: list[int]
    length_bytes: int
    parity_ok: bool
    tail_ok: bool
    reason: str


@dataclass
class ChannelEstimate:
    csi: np.ndarray
    h1: np.ndarray
    h2: np.ndarray
    consistency_error: float


def estimate_channel(corrected_packet: np.ndarray) -> ChannelEstimate:
    x = np.asarray(corrected_packet, dtype=np.complex64)
    if len(x) < 320:
        raise ValueError("packet too short for L-LTF")

    y1 = np.fft.fft(x[192:256], n=64) / 64.0 * np.sqrt(52.0)
    y2 = np.fft.fft(x[256:320], n=64) / 64.0 * np.sqrt(52.0)
    seq = l_ltf_freq_map()

    h1 = []
    h2 = []
    for sc in ACTIVE_SUBCARRIERS:
        ref = seq[int(sc)]
        h1.append(y1[sc_to_bin(int(sc))] / ref)
        h2.append(y2[sc_to_bin(int(sc))] / ref)

    h1 = np.asarray(h1, dtype=np.complex64)
    h2 = np.asarray(h2, dtype=np.complex64)
    h = 0.5 * (h1 + h2)
    err = float(
        np.sum(np.abs(h1 - h2) ** 2)
        / (np.sum(np.abs(h1) ** 2) + np.sum(np.abs(h2) ** 2) + 1e-18)
    )
    return ChannelEstimate(h, h1, h2, err)


def _channel_bins(csi: np.ndarray) -> np.ndarray:
    h = np.zeros(64, np.complex64)
    for sc, value in zip(ACTIVE_SUBCARRIERS, csi):
        h[sc_to_bin(int(sc))] = value
    return h


def equalize_symbol(symbol80: np.ndarray, csi: np.ndarray) -> np.ndarray:
    if len(symbol80) != 80:
        raise ValueError("OFDM symbol must be 80 samples")
    useful = symbol80[16:80]
    y = np.fft.fft(useful, n=FFT_SIZE) / 64.0 * np.sqrt(52.0)
    h = _channel_bins(csi)
    out = np.zeros(64, np.complex64)
    for sc in ACTIVE_SUBCARRIERS:
        idx = sc_to_bin(int(sc))
        if abs(h[idx]) > 1e-10:
            out[idx] = y[idx] / h[idx]
    return out


def correct_pilot_phase(
    equalized: np.ndarray,
    symbol_index: int,
) -> np.ndarray:
    refs = pilot_values(symbol_index)

    acc = 0j
    for sc, ref in refs.items():
        acc += equalized[sc_to_bin(sc)] * np.conj(ref)

    phase = float(np.angle(acc)) if abs(acc) > 0 else 0.0
    return equalized * np.exp(-1j * phase)


def bpsk_bits(eq: np.ndarray) -> list[int]:
    return [
        1 if eq[sc_to_bin(int(sc))].real >= 0.0 else 0
        for sc in DATA_SUBCARRIERS
    ]


def decode_lsig(corrected_packet: np.ndarray, csi: np.ndarray) -> SignalInfo:
    if len(corrected_packet) < 400:
        return SignalInfo(False, [], 0, False, False, "Truncated L-SIG")

    eq = correct_pilot_phase(
        equalize_symbol(corrected_packet[320:400], csi),
        symbol_index=0,
    )
    coded = deinterleave_bpsk_48(bpsk_bits(eq))
    bits = viterbi_decode_rate_half(coded, force_final_zero=True)
    if len(bits) != 24:
        return SignalInfo(False, [], 0, False, False, "Bad L-SIG length")

    rate = [int(v) for v in bits[:4]]
    reserved = int(bits[4])
    length = sum(int(bits[5 + i]) << i for i in range(12))
    parity_ok = (sum(bits[:17]) & 1) == bits[17]
    tail_ok = all(v == 0 for v in bits[18:24])

    reasons = []
    if rate != [1, 1, 0, 1]:
        reasons.append(f"Unsupported RATE bits {rate}")
    if reserved != 0:
        reasons.append("Reserved bit nonzero")
    if not parity_ok:
        reasons.append("Parity failed")
    if not tail_ok:
        reasons.append("Tail failed")
    if not 1 <= length <= 4095:
        reasons.append(f"Invalid length {length}")

    return SignalInfo(
        valid=not reasons,
        rate_bits=rate,
        length_bytes=int(length),
        parity_ok=bool(parity_ok),
        tail_ok=bool(tail_ok),
        reason="OK" if not reasons else "; ".join(reasons),
    )


def data_symbol_count(psdu_length_bytes: int) -> int:
    return int(math.ceil((16 + 8 * psdu_length_bytes + 6) / 24.0))


def decode_psdu(
    corrected_packet: np.ndarray,
    csi: np.ndarray,
    psdu_length_bytes: int,
    *,
    scrambler_state: int = 0x5D,
) -> bytes:
    n_sym = data_symbol_count(psdu_length_bytes)
    required = PREAMBLE_AND_SIG_SAMPLES + n_sym * OFDM_SYMBOL_SAMPLES
    if len(corrected_packet) < required:
        raise ValueError(f"Truncated DATA: need {required}, got {len(corrected_packet)}")

    coded: list[int] = []
    for i in range(n_sym):
        start = PREAMBLE_AND_SIG_SAMPLES + i * 80
        eq = correct_pilot_phase(
            equalize_symbol(corrected_packet[start:start + 80], csi),
            symbol_index=i + 1,
        )
        coded.extend(deinterleave_bpsk_48(bpsk_bits(eq)))

    decoded_scrambled = viterbi_decode_rate_half(coded, force_final_zero=False)
    decoded = descramble(decoded_scrambled, initial_state=scrambler_state)
    psdu_bits = decoded[16:16 + 8 * psdu_length_bytes]
    if len(psdu_bits) != 8 * psdu_length_bytes:
        raise ValueError("Decoded PSDU length mismatch")
    return bits_to_bytes_lsb(psdu_bits)
