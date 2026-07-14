from __future__ import annotations

import numpy as np

try:
    from ..wifi_legacy_ofdm import generate_l_stf, generate_l_ltf, ltf_freq_sequence
except ImportError:
    from src.python.wifi_sensing.wifi_legacy_ofdm import (
        generate_l_stf,
        generate_l_ltf,
        ltf_freq_sequence,
    )


def l_stf_time() -> np.ndarray:
    return np.asarray(generate_l_stf(), dtype=np.complex64)


def l_ltf_time() -> np.ndarray:
    return np.asarray(generate_l_ltf(), dtype=np.complex64)


def l_ltf_freq_map() -> dict[int, complex]:
    seq = ltf_freq_sequence()
    return {int(k): complex(v) for k, v in seq.items()}
