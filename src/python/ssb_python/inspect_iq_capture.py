#!/usr/bin/env python3
"""
Inspect captured raw IQ files from the Python UHD capture scripts.

Input:
    .npz from test_capture_iq_uhd.py
    or .h5 from capture_iq_blocks_uhd.py

Output:
    Basic metrics and figures:
        - amplitude over samples
        - smoothed power over samples
        - average spectrum
        - block metrics if input is HDF5 blocks

This is an offline diagnostic step before PSS/NID2/timing detection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inspect raw IQ capture files.")

    p.add_argument(
        "--input",
        required=True,
        help="Input .npz or .h5 file.",
    )
    p.add_argument(
        "--out-dir",
        default="results/python_iq_inspection",
        help="Output directory for figures and summary.",
    )
    p.add_argument(
        "--max-samples-plot",
        type=int,
        default=50000,
        help="Maximum number of samples to plot in time domain.",
    )
    p.add_argument(
        "--nfft",
        type=int,
        default=4096,
        help="FFT size for spectrum estimate.",
    )
    p.add_argument(
        "--smooth",
        type=int,
        default=1024,
        help="Moving average window for power smoothing.",
    )

    return p.parse_args()


def load_iq(path: Path) -> tuple[np.ndarray, dict]:
    if path.suffix.lower() == ".npz":
        z = np.load(path, allow_pickle=False)
        waveform = z["waveform"].astype(np.complex64)

        cfg = {}
        if "cfg_json" in z:
            cfg = json.loads(str(z["cfg_json"]))

        # Shape as one block.
        waveform = waveform.reshape(1, -1)
        return waveform, cfg

    if path.suffix.lower() in [".h5", ".hdf5"]:
        with h5py.File(path, "r") as f:
            waveform = f["waveform"][:].astype(np.complex64)
            cfg = {}
            if "cfg_json" in f.attrs:
                cfg = json.loads(f.attrs["cfg_json"])
            if "summary_json" in f.attrs:
                cfg["capture_summary"] = json.loads(f.attrs["summary_json"])

        if waveform.ndim == 1:
            waveform = waveform.reshape(1, -1)

        return waveform, cfg

    raise ValueError(f"Unsupported file extension: {path.suffix}")


def moving_average(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    win = min(win, len(x))
    kernel = np.ones(win, dtype=np.float64) / win
    return np.convolve(x, kernel, mode="same")


def estimate_average_spectrum(waveform: np.ndarray, nfft: int) -> tuple[np.ndarray, np.ndarray]:
    blocks = waveform.reshape(-1, waveform.shape[-1])

    spectra = []
    window = np.hanning(nfft).astype(np.float32)

    for block in blocks:
        if len(block) < nfft:
            continue

        # Use non-overlapping chunks to keep this simple and fast.
        n_chunks = len(block) // nfft
        trimmed = block[: n_chunks * nfft].reshape(n_chunks, nfft)

        xw = trimmed * window[None, :]
        spec = np.fft.fftshift(np.fft.fft(xw, axis=1), axes=1)
        psd = np.mean(np.abs(spec) ** 2, axis=0)
        spectra.append(psd)

    if not spectra:
        raise RuntimeError("Not enough samples to compute spectrum.")

    psd_avg = np.mean(np.stack(spectra, axis=0), axis=0)
    psd_db = 10 * np.log10(psd_avg + 1e-12)

    freq_norm = np.linspace(-0.5, 0.5, nfft, endpoint=False)
    return freq_norm, psd_db


def main() -> None:
    args = parse_args()

    in_path = Path(args.input)
    out_dir = Path(args.out_dir) / in_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    waveform, cfg = load_iq(in_path)

    n_blocks, n_samples = waveform.shape

    abs_w = np.abs(waveform)
    power = abs_w**2

    metrics = {
        "input": str(in_path),
        "n_blocks": int(n_blocks),
        "n_samples_per_block": int(n_samples),
        "dtype": str(waveform.dtype),
        "mean_abs": float(abs_w.mean()),
        "rms_abs": float(np.sqrt(np.mean(power))),
        "max_abs": float(abs_w.max()),
        "sat_real_gt_0p99_percent": float(np.mean(np.abs(waveform.real) > 0.99) * 100),
        "sat_imag_gt_0p99_percent": float(np.mean(np.abs(waveform.imag) > 0.99) * 100),
        "sat_abs_gt_1p30_percent": float(np.mean(abs_w > 1.30) * 100),
    }

    if cfg:
        metrics["cfg"] = cfg

    block_metrics = []
    for i in range(n_blocks):
        bw = waveform[i]
        ba = np.abs(bw)
        bp = ba**2
        block_metrics.append(
            {
                "block": i,
                "mean_abs": float(ba.mean()),
                "rms_abs": float(np.sqrt(np.mean(bp))),
                "max_abs": float(ba.max()),
                "sat_real_gt_0p99_percent": float(np.mean(np.abs(bw.real) > 0.99) * 100),
                "sat_imag_gt_0p99_percent": float(np.mean(np.abs(bw.imag) > 0.99) * 100),
            }
        )

    metrics["block_metrics"] = block_metrics

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print("=== IQ inspection ===")
    print(f"input:              {in_path}")
    print(f"n_blocks:           {n_blocks}")
    print(f"n_samples/block:    {n_samples}")
    print(f"mean_abs:           {metrics['mean_abs']:.6f}")
    print(f"rms_abs:            {metrics['rms_abs']:.6f}")
    print(f"max_abs:            {metrics['max_abs']:.6f}")
    print(f"sat real >0.99 [%]: {metrics['sat_real_gt_0p99_percent']:.4f}")
    print(f"sat imag >0.99 [%]: {metrics['sat_imag_gt_0p99_percent']:.4f}")
    print(f"summary:            {summary_path}")

    # Plot first block amplitude.
    x0 = waveform[0]
    n_plot = min(args.max_samples_plot, len(x0))
    idx = np.arange(n_plot)

    plt.figure(figsize=(12, 4))
    plt.plot(idx, np.abs(x0[:n_plot]))
    plt.xlabel("Sample index")
    plt.ylabel("|IQ|")
    plt.title("Amplitude over samples, block 0")
    plt.tight_layout()
    plt.savefig(out_dir / "amplitude_block0.png", dpi=160)
    plt.close()

    # Plot smoothed power first block.
    p0 = np.abs(x0) ** 2
    p0_smooth = moving_average(p0, args.smooth)

    plt.figure(figsize=(12, 4))
    plt.plot(idx, p0_smooth[:n_plot])
    plt.xlabel("Sample index")
    plt.ylabel("Smoothed power")
    plt.title(f"Smoothed power over samples, block 0, window={args.smooth}")
    plt.tight_layout()
    plt.savefig(out_dir / "smoothed_power_block0.png", dpi=160)
    plt.close()

    # Plot block metrics if more than one block.
    if n_blocks > 1:
        block_idx = np.arange(n_blocks)
        mean_abs = np.array([m["mean_abs"] for m in block_metrics])
        rms_abs = np.array([m["rms_abs"] for m in block_metrics])
        max_abs = np.array([m["max_abs"] for m in block_metrics])

        plt.figure(figsize=(10, 4))
        plt.plot(block_idx, mean_abs, marker="o")
        plt.xlabel("Block index")
        plt.ylabel("Mean |IQ|")
        plt.title("Mean amplitude per captured block")
        plt.tight_layout()
        plt.savefig(out_dir / "mean_abs_per_block.png", dpi=160)
        plt.close()

        plt.figure(figsize=(10, 4))
        plt.plot(block_idx, rms_abs, marker="o")
        plt.xlabel("Block index")
        plt.ylabel("RMS |IQ|")
        plt.title("RMS amplitude per captured block")
        plt.tight_layout()
        plt.savefig(out_dir / "rms_abs_per_block.png", dpi=160)
        plt.close()

        plt.figure(figsize=(10, 4))
        plt.plot(block_idx, max_abs, marker="o")
        plt.xlabel("Block index")
        plt.ylabel("Max |IQ|")
        plt.title("Max amplitude per captured block")
        plt.tight_layout()
        plt.savefig(out_dir / "max_abs_per_block.png", dpi=160)
        plt.close()

    # Spectrum.
    freq_norm, psd_db = estimate_average_spectrum(waveform, args.nfft)

    plt.figure(figsize=(10, 4))
    plt.plot(freq_norm, psd_db)
    plt.xlabel("Normalized frequency")
    plt.ylabel("Power [dB, arbitrary]")
    plt.title(f"Average spectrum, NFFT={args.nfft}")
    plt.tight_layout()
    plt.savefig(out_dir / "average_spectrum.png", dpi=160)
    plt.close()

    print("\nGenerated figures:")
    for p in sorted(out_dir.glob("*.png")):
        print(f"  {p}")


if __name__ == "__main__":
    main()
