#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .wlan_rx.detection import detect_stf, stf_metric
from .wlan_rx.sequences import l_ltf_time
from .wlan_rx.common import normalized_correlation


def load_iq(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        return np.asarray(np.load(path), np.complex64)
    d = np.load(path, allow_pickle=False)
    return np.asarray(d["iq"], np.complex64)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--center-sample", type=int, required=True)
    p.add_argument("--radius", type=int, default=12000)
    p.add_argument("--rate", type=float, default=20e6)
    args = p.parse_args()

    iq = load_iq(Path(args.input))
    a = max(0, args.center_sample - args.radius)
    b = min(len(iq), args.center_sample + args.radius)
    block = iq[a:b]

    candidates = detect_stf(
        block,
        sample_rate=args.rate,
        threshold=0.45,
        min_plateau=16,
        min_separation=400,
    )
    print(f"Window: [{a}, {b})")
    print(f"Candidates: {len(candidates)}")
    for i, c in enumerate(candidates[:30]):
        print(
            f"{i:2d}: local={c.coarse_offset:6d} "
            f"global={a+c.coarse_offset:10d} "
            f"metric={c.metric:.3f} "
            f"cfo={c.coarse_cfo_hz:.1f}Hz "
            f"plateau=[{c.plateau_start},{c.plateau_end}]"
        )

    # Brute-force known L-LTF diagnostic without claiming packet identity.
    ltf = l_ltf_time()
    best_metric = -1.0
    best_pos = None
    for pos in range(0, max(0, len(block)-len(ltf))):
        m = normalized_correlation(ltf, block[pos:pos+len(ltf)])
        if m > best_metric:
            best_metric = m
            best_pos = pos

    print(f"Best raw L-LTF metric: {best_metric:.6f}")
    if best_pos is not None:
        print(f"Best raw L-LTF local start: {best_pos}")
        print(f"Best raw L-LTF global start: {a+best_pos}")
        print(f"Implied packet start: {a+best_pos-160}")


if __name__ == "__main__":
    main()
