from __future__ import annotations

import numpy as np

SAMPLE_RATE = 20e6
FFT_SIZE = 64
CP_LEN = 16
OFDM_SYMBOL_SAMPLES = 80
L_STF_SAMPLES = 160
L_LTF_SAMPLES = 160
L_SIG_SAMPLES = 80
PREAMBLE_SAMPLES = 320
PREAMBLE_AND_SIG_SAMPLES = 400

ACTIVE_SUBCARRIERS = np.array(list(range(-26, 0)) + list(range(1, 27)), dtype=np.int16)
DATA_SUBCARRIERS = np.array(
    [k for k in ACTIVE_SUBCARRIERS if k not in (-21, -7, 7, 21)],
    dtype=np.int16,
)
PILOT_SUBCARRIERS = np.array([-21, -7, 7, 21], dtype=np.int16)


def sc_to_bin(sc: int) -> int:
    return int(sc) % FFT_SIZE


def moving_sum(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 0:
        raise ValueError("win must be positive")
    x = np.asarray(x)
    if len(x) < win:
        return np.zeros(0, dtype=x.dtype)
    c = np.concatenate([np.zeros(1, dtype=x.dtype), np.cumsum(x, dtype=x.dtype)])
    return c[win:] - c[:-win]


def correct_cfo(
    x: np.ndarray,
    cfo_hz: float,
    sample_rate: float = SAMPLE_RATE,
    start_index: int = 0,
) -> np.ndarray:
    x = np.asarray(x, dtype=np.complex64)
    n = np.arange(start_index, start_index + len(x), dtype=np.float64)
    rot = np.exp(-1j * 2.0 * np.pi * float(cfo_hz) * n / float(sample_rate))
    return (x * rot).astype(np.complex64)


def normalized_correlation(template: np.ndarray, segment: np.ndarray) -> float:
    template = np.asarray(template, dtype=np.complex64)
    segment = np.asarray(segment, dtype=np.complex64)
    if len(template) != len(segment) or len(template) == 0:
        return 0.0
    et = float(np.vdot(template, template).real)
    es = float(np.vdot(segment, segment).real)
    if et <= 0.0 or es <= 0.0:
        return 0.0
    return float(np.abs(np.vdot(template, segment)) ** 2 / (et * es + 1e-18))
