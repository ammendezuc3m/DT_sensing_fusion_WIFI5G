#!/usr/bin/env python3
"""
Golay complementary sequence utilities.

The DMG/EDMG family uses Golay-like complementary sequences for synchronization,
channel estimation and training. This module provides deterministic Golay pairs
for the experimental DMG-like sensing PPDU.

The generated pairs are not claimed to be the exact standardized IEEE sequence
tables. They are valid complementary Golay pairs used to build a DMG-inspired
PHY waveform for USRP sensing experiments.
"""

from __future__ import annotations

import numpy as np


def is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def golay_pair(length: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate a real BPSK Golay complementary pair of the requested power-of-two length.

    Recursive construction:
        A' = [A,  B]
        B' = [A, -B]

    Returns:
        a, b as float32 arrays with values +1/-1.
    """
    if not is_power_of_two(length):
        raise ValueError(f"Golay length must be a power of two, got {length}")

    a = np.array([1.0], dtype=np.float32)
    b = np.array([1.0], dtype=np.float32)

    while len(a) < length:
        a_old = a
        b_old = b

        a = np.concatenate([a_old, b_old]).astype(np.float32)
        b = np.concatenate([a_old, -b_old]).astype(np.float32)

    return a, b


def aperiodic_autocorr(x: np.ndarray) -> np.ndarray:
    """
    Full aperiodic autocorrelation.
    """
    x = np.asarray(x)
    return np.correlate(x, x, mode="full")


def complementary_sidelobe_check(a: np.ndarray, b: np.ndarray) -> dict:
    """
    Check complementary property.

    For a Golay pair, autocorr(a) + autocorr(b) should have zero sidelobes.
    """
    ca = aperiodic_autocorr(a)
    cb = aperiodic_autocorr(b)
    c = ca + cb

    center = len(c) // 2
    peak = float(c[center])

    sidelobes = np.concatenate([c[:center], c[center + 1:]])
    max_sidelobe = float(np.max(np.abs(sidelobes))) if len(sidelobes) else 0.0

    return {
        "length": int(len(a)),
        "peak": peak,
        "max_sidelobe_abs": max_sidelobe,
        "max_sidelobe_ratio": max_sidelobe / max(abs(peak), 1e-12),
    }


def bpsk_to_complex(x: np.ndarray) -> np.ndarray:
    """
    Convert real BPSK sequence to complex64 baseband samples.
    """
    return np.asarray(x, dtype=np.float32).astype(np.complex64)


def make_sign_code(length: int, seed: int) -> np.ndarray:
    """
    Deterministic +/-1 sign code.
    """
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=length, dtype=np.int8)
    signs = 2.0 * bits.astype(np.float32) - 1.0
    return signs.astype(np.float32)


def repeat_to_length(x: np.ndarray, length: int) -> np.ndarray:
    """
    Repeat sequence until length is reached, then truncate.
    """
    x = np.asarray(x)
    reps = int(np.ceil(length / len(x)))
    return np.tile(x, reps)[:length]


def main() -> None:
    for n in [128, 512]:
        a, b = golay_pair(n)
        chk = complementary_sidelobe_check(a, b)
        print(f"Golay pair length {n}: {chk}")


if __name__ == "__main__":
    main()
