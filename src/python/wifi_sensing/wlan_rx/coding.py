from __future__ import annotations

import numpy as np


def parity(value: int) -> int:
    return int(value).bit_count() & 1


def viterbi_decode_rate_half(
    coded_bits: list[int],
    *,
    force_final_zero: bool = False,
) -> list[int]:
    if len(coded_bits) % 2:
        raise ValueError("coded bit count must be even")

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
        next_metrics = np.full(128, inf, dtype=np.int32)

        for state in range(128):
            base = int(metrics[state])
            if base >= inf:
                continue
            for bit in (0, 1):
                ns = ((state >> 1) | ((bit & 1) << 6)) & 0x7F
                o0 = parity(ns & g0)
                o1 = parity(ns & g1)
                score = base + (o0 != r0) + (o1 != r1)
                if score < next_metrics[ns]:
                    next_metrics[ns] = score
                    prev_state[t, ns] = state
                    prev_bit[t, ns] = bit
        metrics = next_metrics

    state = 0 if force_final_zero else int(np.argmin(metrics))
    decoded = [0] * n_steps
    for t in range(n_steps - 1, -1, -1):
        bit = int(prev_bit[t, state])
        ps = int(prev_state[t, state])
        if bit < 0 or ps < 0:
            raise ValueError("Viterbi traceback failed")
        decoded[t] = bit
        state = ps
    return decoded


def deinterleave_bpsk_48(bits: list[int]) -> list[int]:
    if len(bits) != 48:
        raise ValueError("Expected 48 bits")
    out = [0] * 48
    for k in range(48):
        i = 3 * (k % 16) + (k // 16)
        out[k] = int(bits[i])
    return out


def descramble(bits: list[int], initial_state: int = 0x5D) -> list[int]:
    if not 1 <= initial_state <= 0x7F:
        raise ValueError("invalid scrambler state")
    state = [(initial_state >> i) & 1 for i in range(7)]
    out = []
    for bit in bits:
        feedback = state[6] ^ state[3]
        out.append(int(bit) ^ feedback)
        state = [feedback] + state[:6]
    return out


def bits_to_bytes_lsb(bits: list[int]) -> bytes:
    if len(bits) % 8:
        raise ValueError("bit count must be multiple of 8")
    out = bytearray()
    for i in range(0, len(bits), 8):
        value = 0
        for j, bit in enumerate(bits[i:i + 8]):
            value |= (int(bit) & 1) << j
        out.append(value)
    return bytes(out)
