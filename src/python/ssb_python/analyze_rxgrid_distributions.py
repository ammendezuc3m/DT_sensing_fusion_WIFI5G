#!/usr/bin/env python3
"""
Analyze amplitude and phase distributions of rxGridSSB/dataSSB datasets.

Supports HDF5 files with datasets:
    rxGridSSB
    dataSSB

Expected shapes:
    rxGridSSB: 240 x 4 x N
    dataSSB:   360 x 6 x N
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze rxGridSSB/dataSSB amplitude and phase distributions.")

    p.add_argument("--input", required=True, help="Input .h5 file.")
    p.add_argument("--dataset", default="rxGridSSB", choices=["rxGridSSB", "dataSSB"])
    p.add_argument("--valid-mask-dataset", default="valid_mask")
    p.add_argument("--out-dir", default="results/python_rxgrid_distribution")
    p.add_argument("--label", default="")
    p.add_argument("--max-scatter-points", type=int, default=50000)

    return p.parse_args()


def circular_stats(phase: np.ndarray) -> dict:
    z = np.exp(1j * phase.reshape(-1))
    mean_z = np.mean(z)
    circ_mean = float(np.angle(mean_z))
    resultant_length = float(np.abs(mean_z))

    return {
        "circular_mean_rad": circ_mean,
        "resultant_length": resultant_length,
    }


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)

    with h5py.File(input_path, "r") as f:
        x = f[args.dataset][:].astype(np.complex64)

        valid_mask = None
        if args.valid_mask_dataset in f and x.ndim == 3:
            valid_mask = f[args.valid_mask_dataset][:].astype(bool)

    if x.ndim == 2:
        x_valid = x[:, :, None]
    elif x.ndim == 3:
        if valid_mask is not None and len(valid_mask) == x.shape[2]:
            x_valid = x[:, :, valid_mask]
        else:
            x_valid = x
    else:
        raise RuntimeError(f"Unsupported dataset shape: {x.shape}")

    if x_valid.shape[2] == 0:
        raise RuntimeError("No valid samples available.")

    amp = np.abs(x_valid)
    amp_db = 20.0 * np.log10(amp + 1e-9)
    phase = np.angle(x_valid)
    real = np.real(x_valid)
    imag = np.imag(x_valid)

    stats = {
        "input": str(input_path),
        "dataset": args.dataset,
        "label": args.label,
        "shape_original": list(x.shape),
        "shape_used": list(x_valid.shape),
        "n_valid_samples": int(x_valid.shape[2]),
        "amplitude": {
            "mean": float(np.mean(amp)),
            "std": float(np.std(amp)),
            "min": float(np.min(amp)),
            "p01": float(np.percentile(amp, 1)),
            "p05": float(np.percentile(amp, 5)),
            "p25": float(np.percentile(amp, 25)),
            "median": float(np.median(amp)),
            "p75": float(np.percentile(amp, 75)),
            "p95": float(np.percentile(amp, 95)),
            "p99": float(np.percentile(amp, 99)),
            "max": float(np.max(amp)),
        },
        "amplitude_db": {
            "mean": float(np.mean(amp_db)),
            "std": float(np.std(amp_db)),
            "min": float(np.min(amp_db)),
            "p01": float(np.percentile(amp_db, 1)),
            "p05": float(np.percentile(amp_db, 5)),
            "p25": float(np.percentile(amp_db, 25)),
            "median": float(np.median(amp_db)),
            "p75": float(np.percentile(amp_db, 75)),
            "p95": float(np.percentile(amp_db, 95)),
            "p99": float(np.percentile(amp_db, 99)),
            "max": float(np.max(amp_db)),
        },
        "phase": {
            "mean_linear_wrong_but_reported": float(np.mean(phase)),
            "std_linear": float(np.std(phase)),
            "min": float(np.min(phase)),
            "max": float(np.max(phase)),
            **circular_stats(phase),
        },
        "real": {
            "mean": float(np.mean(real)),
            "std": float(np.std(real)),
            "min": float(np.min(real)),
            "max": float(np.max(real)),
        },
        "imag": {
            "mean": float(np.mean(imag)),
            "std": float(np.std(imag)),
            "min": float(np.min(imag)),
            "max": float(np.max(imag)),
        },
    }

    label = args.label if args.label else input_path.stem
    safe_label = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)

    out_dir = Path(args.out_dir) / f"{safe_label}_{args.dataset}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "distribution_summary.json"
    summary_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    print("=== Distribution analysis ===")
    print(f"input:         {input_path}")
    print(f"dataset:       {args.dataset}")
    print(f"shape original:{x.shape}")
    print(f"shape used:    {x_valid.shape}")
    print(f"summary:       {summary_path}")

    print("\nAmplitude:")
    print(f"  mean:   {stats['amplitude']['mean']:.6f}")
    print(f"  median: {stats['amplitude']['median']:.6f}")
    print(f"  p05:    {stats['amplitude']['p05']:.6f}")
    print(f"  p95:    {stats['amplitude']['p95']:.6f}")
    print(f"  max:    {stats['amplitude']['max']:.6f}")

    print("\nAmplitude dB:")
    print(f"  mean:   {stats['amplitude_db']['mean']:.3f} dB")
    print(f"  median: {stats['amplitude_db']['median']:.3f} dB")
    print(f"  p05:    {stats['amplitude_db']['p05']:.3f} dB")
    print(f"  p95:    {stats['amplitude_db']['p95']:.3f} dB")

    print("\nPhase:")
    print(f"  circular mean:      {stats['phase']['circular_mean_rad']:.6f} rad")
    print(f"  resultant length:   {stats['phase']['resultant_length']:.6f}")

    # Hist amplitude linear.
    plt.figure(figsize=(8, 4))
    plt.hist(amp.reshape(-1), bins=120)
    plt.xlabel("|grid|")
    plt.ylabel("Count")
    plt.title(f"{args.dataset} amplitude distribution")
    plt.tight_layout()
    plt.savefig(out_dir / "hist_amplitude_linear.png", dpi=160)
    plt.close()

    # Hist amplitude dB.
    plt.figure(figsize=(8, 4))
    plt.hist(amp_db.reshape(-1), bins=120)
    plt.xlabel("20log10(|grid|)")
    plt.ylabel("Count")
    plt.title(f"{args.dataset} amplitude distribution [dB]")
    plt.tight_layout()
    plt.savefig(out_dir / "hist_amplitude_db.png", dpi=160)
    plt.close()

    # Hist phase.
    plt.figure(figsize=(8, 4))
    plt.hist(phase.reshape(-1), bins=120, range=(-np.pi, np.pi))
    plt.xlabel("Phase [rad]")
    plt.ylabel("Count")
    plt.title(f"{args.dataset} phase distribution")
    plt.tight_layout()
    plt.savefig(out_dir / "hist_phase.png", dpi=160)
    plt.close()

    # IQ scatter.
    r = real.reshape(-1)
    im = imag.reshape(-1)

    n_points = len(r)
    if n_points > args.max_scatter_points:
        rng = np.random.default_rng(1234)
        idx = rng.choice(n_points, size=args.max_scatter_points, replace=False)
        r = r[idx]
        im = im[idx]

    plt.figure(figsize=(6, 6))
    plt.scatter(r, im, s=1, alpha=0.25)
    plt.xlabel("Real")
    plt.ylabel("Imag")
    plt.title(f"{args.dataset} IQ scatter")
    plt.axis("equal")
    plt.tight_layout()
    plt.savefig(out_dir / "iq_scatter.png", dpi=160)
    plt.close()

    # Mean amplitude heatmap.
    mean_amp = np.mean(amp, axis=2)
    plt.figure(figsize=(9, 4))
    plt.imshow(20.0 * np.log10(mean_amp + 1e-9).T, aspect="auto", origin="lower")
    plt.colorbar(label="Mean magnitude [dB]")
    plt.xlabel("Subcarrier index")
    plt.ylabel("OFDM symbol index")
    plt.title(f"{args.dataset} mean amplitude heatmap")
    plt.tight_layout()
    plt.savefig(out_dir / "mean_amplitude_heatmap_db.png", dpi=160)
    plt.close()

    # Mean by subcarrier.
    mean_by_sc = np.mean(amp, axis=(1, 2))
    plt.figure(figsize=(9, 4))
    plt.plot(mean_by_sc)
    plt.xlabel("Subcarrier index")
    plt.ylabel("Mean |grid|")
    plt.title(f"{args.dataset} mean amplitude by subcarrier")
    plt.tight_layout()
    plt.savefig(out_dir / "mean_amplitude_by_subcarrier.png", dpi=160)
    plt.close()

    # Mean by symbol.
    mean_by_sym = np.mean(amp, axis=(0, 2))
    plt.figure(figsize=(7, 4))
    plt.plot(np.arange(len(mean_by_sym)), mean_by_sym, marker="o")
    plt.xlabel("OFDM symbol index")
    plt.ylabel("Mean |grid|")
    plt.title(f"{args.dataset} mean amplitude by OFDM symbol")
    plt.tight_layout()
    plt.savefig(out_dir / "mean_amplitude_by_symbol.png", dpi=160)
    plt.close()

    print("\nGenerated figures:")
    for p in sorted(out_dir.glob("*.png")):
        print(f"  {p}")


if __name__ == "__main__":
    main()
