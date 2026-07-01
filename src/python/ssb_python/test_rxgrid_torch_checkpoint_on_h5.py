#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import h5py
import numpy as np

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from rxgrid_torch_inference import RxGridTorchBinaryModel


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model-pt", default="results/binary_empty_vs_P5_rx/model_rxGridSSB/model.pt")
    p.add_argument("--input-h5", required=True)
    p.add_argument("--max-samples", type=int, default=30)
    return p.parse_args()


def load_rxgrid_h5(path: Path) -> np.ndarray:
    with h5py.File(path, "r") as f:
        x = f["rxGridSSB"][:].astype(np.complex64)

        if "valid_mask" in f:
            mask = f["valid_mask"][:].astype(bool)
            if x.ndim == 3 and len(mask) == x.shape[2]:
                x = x[:, :, mask]

    if x.shape[0] != 240 or x.shape[1] != 4:
        raise RuntimeError(f"Expected rxGridSSB [240,4,N], got {x.shape}")

    return x


def main():
    args = parse_args()

    model = RxGridTorchBinaryModel(args.model_pt, device="cpu")
    rx = load_rxgrid_h5(Path(args.input_h5))

    if args.max_samples > 0:
        rx = rx[:, :, : args.max_samples]

    print("=== Torch checkpoint rxGridSSB test ===")
    print("model:", args.model_pt)
    print("input:", args.input_h5)
    print("rxGridSSB:", rx.shape)
    print("classes:", model.classes)

    labels = []
    probs_p5 = []

    for i in range(rx.shape[2]):
        pred = model.predict_proba(rx[:, :, i])
        labels.append(pred["label"])
        probs_p5.append(pred["probabilities"][model.classes[1]])

        print(
            f"[{i:04d}] "
            f"label={pred['label']} "
            f"conf={pred['confidence']:.4f} "
            f"p_empty={pred['probabilities'][model.classes[0]]:.4f} "
            f"p_p5={pred['probabilities'][model.classes[1]]:.4f} "
            f"rx_mean={pred['features']['rxGridSSB_mean_abs']:.3f}"
        )

    unique, counts = np.unique(labels, return_counts=True)
    print("\nCounts:")
    for u, c in zip(unique, counts):
        print(f"  {u}: {c}")

    print("\nP5 probability:")
    print("  mean:", float(np.mean(probs_p5)))
    print("  min: ", float(np.min(probs_p5)))
    print("  max: ", float(np.max(probs_p5)))


if __name__ == "__main__":
    main()
