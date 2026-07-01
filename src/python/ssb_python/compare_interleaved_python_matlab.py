#!/usr/bin/env python3
"""
Compare interleaved Python-CFO and MATLAB SSB captures.

Inputs:
    --python-pre   Python H5 with rxGridSSB = 240 x 4 x N
    --matlab-mat   MATLAB .mat with dataSSB = 360 x 6 x N or rxGridSSB = 240 x 4 x N
    --python-post  Python H5 with rxGridSSB = 240 x 4 x N

Output:
    Overlay plots and JSON summary:
        amplitude dB histogram
        phase histogram
        mean amplitude by subcarrier
        mean amplitude by OFDM symbol
        mean amplitude heatmaps
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import loadmat


def parse_args():
    p = argparse.ArgumentParser(description="Compare Python pre/post vs MATLAB rxGridSSB distributions.")
    p.add_argument("--python-pre", required=True)
    p.add_argument("--matlab-mat", required=True)
    p.add_argument("--python-post", required=True)
    p.add_argument("--out-dir", default="results/python_matlab_rxgrid_compare/interleaved")
    p.add_argument("--normalize", choices=["none", "median"], default="none")
    p.add_argument("--max-samples", type=int, default=0, help="0 means use all valid samples.")
    return p.parse_args()


def read_h5_complex_dataset(ds):
    x = ds[()]

    if np.iscomplexobj(x):
        return x.astype(np.complex64)

    if x.dtype.fields is not None:
        fields = list(x.dtype.fields.keys())
        low = [f.lower() for f in fields]
        if "real" in low and "imag" in low:
            r = fields[low.index("real")]
            im = fields[low.index("imag")]
            return (x[r] + 1j * x[im]).astype(np.complex64)
        if len(fields) >= 2:
            return (x[fields[0]] + 1j * x[fields[1]]).astype(np.complex64)

    return x.astype(np.complex64)


def list_h5_datasets(path: Path):
    keys = []

    def visitor(name, obj):
        if isinstance(obj, h5py.Dataset):
            keys.append(name)

    with h5py.File(path, "r") as f:
        f.visititems(visitor)

    return keys


def orient_rxgrid(x):
    x = np.asarray(x)
    x = np.squeeze(x)

    if x.ndim == 2:
        x = x[:, :, None]

    if x.ndim != 3:
        raise RuntimeError(f"rxGridSSB must be 3D after squeeze, got {x.shape}")

    candidates = [
        (x, x.shape),
        (np.transpose(x, (1, 0, 2)), np.transpose(x, (1, 0, 2)).shape),
        (np.transpose(x, (2, 1, 0)), np.transpose(x, (2, 1, 0)).shape),
        (np.transpose(x, (1, 2, 0)), np.transpose(x, (1, 2, 0)).shape),
        (np.transpose(x, (2, 0, 1)), np.transpose(x, (2, 0, 1)).shape),
        (np.transpose(x, (0, 2, 1)), np.transpose(x, (0, 2, 1)).shape),
    ]

    for y, shape in candidates:
        if shape[0] == 240 and shape[1] == 4:
            return y.astype(np.complex64)

    raise RuntimeError(f"Could not orient rxGridSSB to 240x4xN. Original shape: {x.shape}")


def orient_datassb(x):
    x = np.asarray(x)
    x = np.squeeze(x)

    if x.ndim == 2:
        x = x[:, :, None]

    if x.ndim != 3:
        raise RuntimeError(f"dataSSB must be 3D after squeeze, got {x.shape}")

    candidates = [
        (x, x.shape),
        (np.transpose(x, (1, 0, 2)), np.transpose(x, (1, 0, 2)).shape),
        (np.transpose(x, (2, 1, 0)), np.transpose(x, (2, 1, 0)).shape),
        (np.transpose(x, (1, 2, 0)), np.transpose(x, (1, 2, 0)).shape),
        (np.transpose(x, (2, 0, 1)), np.transpose(x, (2, 0, 1)).shape),
        (np.transpose(x, (0, 2, 1)), np.transpose(x, (0, 2, 1)).shape),
    ]

    for y, shape in candidates:
        if shape[0] == 360 and shape[1] == 6:
            return y.astype(np.complex64)

    raise RuntimeError(f"Could not orient dataSSB to 360x6xN. Original shape: {x.shape}")


def load_python_h5_rxgrid(path: Path):
    with h5py.File(path, "r") as f:
        x = f["rxGridSSB"][:].astype(np.complex64)
        x = orient_rxgrid(x)

        if "valid_mask" in f:
            mask = f["valid_mask"][:].astype(bool)
            if len(mask) == x.shape[2]:
                x = x[:, :, mask]

    return x


def load_matlab_rxgrid(path: Path):
    # Try v7.3/HDF5 first.
    try:
        keys = list_h5_datasets(path)

        with h5py.File(path, "r") as f:
            selected = None
            for k in ["rxGridSSB", "dataSSB"]:
                if k in f:
                    selected = k
                    break

            if selected is None:
                for k in keys:
                    if k.endswith("rxGridSSB") or k.endswith("dataSSB"):
                        selected = k
                        break

            if selected is None:
                raise RuntimeError(f"No rxGridSSB/dataSSB found. HDF5 datasets: {keys}")

            x = read_h5_complex_dataset(f[selected])

            valid_mask = None
            for mask_name in ["validMask", "valid_mask"]:
                if mask_name in f:
                    valid_mask = np.asarray(f[mask_name][()]).astype(bool).reshape(-1)
                    break

    except OSError:
        # Classic MAT.
        m = loadmat(path)
        keys = [k for k in m.keys() if not k.startswith("__")]
        selected = None
        for k in ["rxGridSSB", "dataSSB"]:
            if k in m:
                selected = k
                break
        if selected is None:
            raise RuntimeError(f"No rxGridSSB/dataSSB found. MAT variables: {keys}")
        x = m[selected]
        valid_mask = None
        for mask_name in ["validMask", "valid_mask"]:
            if mask_name in m:
                valid_mask = np.asarray(m[mask_name]).astype(bool).reshape(-1)
                break

    if "rxGridSSB" in selected:
        rx = orient_rxgrid(x)
    else:
        data = orient_datassb(x)
        rx = data[60:300, 1:5, :]

    if valid_mask is not None and len(valid_mask) == rx.shape[2]:
        rx = rx[:, :, valid_mask]

    return rx.astype(np.complex64), selected


def limit_samples(x, max_samples):
    if max_samples <= 0 or x.shape[2] <= max_samples:
        return x
    return x[:, :, :max_samples]


def normalize_grid(x, mode):
    if mode == "none":
        return x
    if mode == "median":
        med = np.median(np.abs(x))
        if med > 0:
            return x / med
    return x


def stats(x):
    amp = np.abs(x)
    amp_db = 20 * np.log10(amp + 1e-9)
    phase = np.angle(x)
    z = np.exp(1j * phase.reshape(-1))
    mz = np.mean(z)

    return {
        "shape": list(x.shape),
        "amplitude": {
            "mean": float(np.mean(amp)),
            "median": float(np.median(amp)),
            "std": float(np.std(amp)),
            "p05": float(np.percentile(amp, 5)),
            "p95": float(np.percentile(amp, 95)),
            "max": float(np.max(amp)),
        },
        "amplitude_db": {
            "mean": float(np.mean(amp_db)),
            "median": float(np.median(amp_db)),
            "std": float(np.std(amp_db)),
            "p05": float(np.percentile(amp_db, 5)),
            "p95": float(np.percentile(amp_db, 95)),
        },
        "phase": {
            "circular_mean_rad": float(np.angle(mz)),
            "resultant_length": float(np.abs(mz)),
            "linear_std": float(np.std(phase)),
        },
    }


def plot_heatmap(out_dir, name, x, title):
    mean_amp = np.mean(np.abs(x), axis=2)
    plt.figure(figsize=(9, 4))
    plt.imshow(20 * np.log10(mean_amp + 1e-9).T, aspect="auto", origin="lower")
    plt.colorbar(label="Mean magnitude [dB]")
    plt.xlabel("Subcarrier index")
    plt.ylabel("OFDM symbol index")
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_dir / name, dpi=160)
    plt.close()


def main():
    args = parse_args()

    py_pre_path = Path(args.python_pre)
    py_post_path = Path(args.python_post)
    mat_path = Path(args.matlab_mat)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    py_pre = load_python_h5_rxgrid(py_pre_path)
    py_post = load_python_h5_rxgrid(py_post_path)
    mat, mat_var = load_matlab_rxgrid(mat_path)

    py_pre = limit_samples(py_pre, args.max_samples)
    py_post = limit_samples(py_post, args.max_samples)
    mat = limit_samples(mat, args.max_samples)

    py_pre_n = normalize_grid(py_pre, args.normalize)
    py_post_n = normalize_grid(py_post, args.normalize)
    mat_n = normalize_grid(mat, args.normalize)

    datasets = {
        "Python pre CFO": py_pre_n,
        "MATLAB": mat_n,
        "Python post CFO": py_post_n,
    }

    summary = {
        "python_pre": str(py_pre_path),
        "matlab_mat": str(mat_path),
        "python_post": str(py_post_path),
        "matlab_variable_used": mat_var,
        "normalize": args.normalize,
        "max_samples": args.max_samples,
        "stats": {k: stats(v) for k, v in datasets.items()},
    }

    (out_dir / "comparison_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("=== Interleaved Python/MATLAB comparison ===")
    print(f"Python pre:      {py_pre.shape}")
    print(f"MATLAB:          {mat.shape}   variable={mat_var}")
    print(f"Python post:     {py_post.shape}")
    print(f"Normalize:       {args.normalize}")
    print(f"Summary:         {out_dir / 'comparison_summary.json'}")

    for name, st in summary["stats"].items():
        print(f"\n{name}")
        print(f"  amp mean:    {st['amplitude']['mean']:.6f}")
        print(f"  amp median:  {st['amplitude']['median']:.6f}")
        print(f"  amp dB mean: {st['amplitude_db']['mean']:.3f}")
        print(f"  phase R:     {st['phase']['resultant_length']:.6f}")

    # Amplitude dB histogram.
    plt.figure(figsize=(9, 4))
    for name, x in datasets.items():
        amp_db = 20 * np.log10(np.abs(x).reshape(-1) + 1e-9)
        plt.hist(amp_db, bins=140, density=True, alpha=0.45, label=name)
    plt.xlabel("20log10(|rxGridSSB|)")
    plt.ylabel("Density")
    plt.title("Amplitude distribution [dB]")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_hist_amplitude_db.png", dpi=160)
    plt.close()

    # Phase histogram.
    plt.figure(figsize=(9, 4))
    for name, x in datasets.items():
        phase = np.angle(x).reshape(-1)
        plt.hist(phase, bins=140, range=(-np.pi, np.pi), density=True, alpha=0.45, label=name)
    plt.xlabel("Phase [rad]")
    plt.ylabel("Density")
    plt.title("Phase distribution")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_hist_phase.png", dpi=160)
    plt.close()

    # Mean amplitude by subcarrier.
    plt.figure(figsize=(10, 4))
    for name, x in datasets.items():
        y = np.mean(np.abs(x), axis=(1, 2))
        plt.plot(y, label=name)
    plt.xlabel("Subcarrier index")
    plt.ylabel("Mean |rxGridSSB|")
    plt.title("Mean amplitude by subcarrier")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_mean_amplitude_by_subcarrier.png", dpi=160)
    plt.close()

    # Mean amplitude by symbol.
    plt.figure(figsize=(8, 4))
    for name, x in datasets.items():
        y = np.mean(np.abs(x), axis=(0, 2))
        plt.plot(np.arange(4), y, marker="o", label=name)
    plt.xlabel("OFDM symbol index")
    plt.ylabel("Mean |rxGridSSB|")
    plt.title("Mean amplitude by OFDM symbol")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_mean_amplitude_by_symbol.png", dpi=160)
    plt.close()

    plot_heatmap(out_dir, "heatmap_python_pre.png", py_pre_n, "Python pre CFO mean rxGridSSB")
    plot_heatmap(out_dir, "heatmap_matlab.png", mat_n, "MATLAB mean rxGridSSB")
    plot_heatmap(out_dir, "heatmap_python_post.png", py_post_n, "Python post CFO mean rxGridSSB")

    print("\nGenerated figures:")
    for p in sorted(out_dir.glob("*.png")):
        print(f"  {p}")


if __name__ == "__main__":
    main()
