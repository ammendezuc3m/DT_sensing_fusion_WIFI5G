#!/usr/bin/env python3
"""
Compare Python-generated rxGridSSB against MATLAB-generated dataSSB/rxGridSSB.

Python input:
    .h5 with rxGridSSB = 240 x 4 x N

MATLAB input:
    .mat containing either:
        rxGridSSB = 240 x 4 x N
    or:
        dataSSB   = 360 x 6 x N

If MATLAB file contains dataSSB, this script computes:
    rxGridSSB = dataSSB(61:300, 2:5)
Python indexing:
    rxGridSSB = dataSSB[60:300, 1:5, :]

Outputs:
    - overlaid amplitude histograms
    - overlaid phase histograms
    - mean amplitude by subcarrier
    - mean amplitude by OFDM symbol
    - summary JSON
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
    p = argparse.ArgumentParser(description="Compare Python and MATLAB rxGridSSB distributions.")

    p.add_argument("--python-h5", required=True, help="Python .h5 file with rxGridSSB.")
    p.add_argument("--matlab-file", required=True, help="MATLAB .mat file with dataSSB or rxGridSSB.")
    p.add_argument("--matlab-dataset", default="", help="Optional MATLAB dataset name.")
    p.add_argument("--out-dir", default="results/python_matlab_rxgrid_compare")
    p.add_argument("--label-python", default="Python")
    p.add_argument("--label-matlab", default="MATLAB")
    p.add_argument("--normalize", choices=["none", "median"], default="none")

    return p.parse_args()


def h5_complex_to_numpy(obj):
    x = obj[()]

    if np.iscomplexobj(x):
        return x.astype(np.complex64)

    if x.dtype.fields is not None:
        fields = list(x.dtype.fields.keys())
        low = [f.lower() for f in fields]

        if "real" in low and "imag" in low:
            r_name = fields[low.index("real")]
            i_name = fields[low.index("imag")]
            return (x[r_name] + 1j * x[i_name]).astype(np.complex64)

        if len(fields) >= 2:
            return (x[fields[0]] + 1j * x[fields[1]]).astype(np.complex64)

    return x.astype(np.complex64)


def orient_data_ssb(x):
    """
    Return dataSSB as 360 x 6 x N.
    """
    x = np.asarray(x)

    if x.ndim == 2:
        x = x[:, :, None]

    x = np.squeeze(x)

    if x.ndim == 2:
        x = x[:, :, None]

    if x.ndim != 3:
        raise RuntimeError(f"Cannot orient dataSSB with shape {x.shape}")

    if x.shape[0] == 360 and x.shape[1] == 6:
        return x

    if x.shape[0] == 6 and x.shape[1] == 360:
        return np.transpose(x, (1, 0, 2))

    if x.shape[1] == 6 and x.shape[2] == 360:
        return np.transpose(x, (2, 1, 0))

    if x.shape[1] == 360 and x.shape[2] == 6:
        return np.transpose(x, (1, 2, 0))

    raise RuntimeError(f"Unsupported dataSSB shape: {x.shape}")


def orient_rx_grid(x):
    """
    Return rxGridSSB as 240 x 4 x N.
    """
    x = np.asarray(x)

    if x.ndim == 2:
        x = x[:, :, None]

    x = np.squeeze(x)

    if x.ndim == 2:
        x = x[:, :, None]

    if x.ndim != 3:
        raise RuntimeError(f"Cannot orient rxGridSSB with shape {x.shape}")

    if x.shape[0] == 240 and x.shape[1] == 4:
        return x

    if x.shape[0] == 4 and x.shape[1] == 240:
        return np.transpose(x, (1, 0, 2))

    if x.shape[1] == 4 and x.shape[2] == 240:
        return np.transpose(x, (2, 1, 0))

    if x.shape[1] == 240 and x.shape[2] == 4:
        return np.transpose(x, (1, 2, 0))

    raise RuntimeError(f"Unsupported rxGridSSB shape: {x.shape}")


def load_python_rxgrid(path):
    with h5py.File(path, "r") as f:
        x = f["rxGridSSB"][:].astype(np.complex64)

        if "valid_mask" in f:
            mask = f["valid_mask"][:].astype(bool)
            if x.ndim == 3 and len(mask) == x.shape[2]:
                x = x[:, :, mask]

    return orient_rx_grid(x)


def list_h5_keys(path):
    keys = []

    def visitor(name, obj):
        if isinstance(obj, h5py.Dataset):
            keys.append(name)

    with h5py.File(path, "r") as f:
        f.visititems(visitor)

    return keys


def load_matlab_array(path, dataset_name):
    """
    Load a MATLAB array from either v7.3 HDF5 .mat or classic .mat.
    """
    # Try HDF5 / v7.3 first.
    try:
        with h5py.File(path, "r") as f:
            keys = list_h5_keys(path)

            if dataset_name:
                if dataset_name not in f:
                    raise RuntimeError(f"Dataset '{dataset_name}' not found. Available: {keys}")
                return h5_complex_to_numpy(f[dataset_name]), dataset_name, keys

            # Prefer rxGridSSB if present, otherwise dataSSB.
            for candidate in ["rxGridSSB", "dataSSB"]:
                if candidate in f:
                    return h5_complex_to_numpy(f[candidate]), candidate, keys

            # Fallback: search by ending name.
            for key in keys:
                if key.endswith("rxGridSSB") or key.endswith("dataSSB"):
                    return h5_complex_to_numpy(f[key]), key, keys

            raise RuntimeError(f"No rxGridSSB/dataSSB dataset found. Available: {keys}")

    except OSError:
        pass

    # Classic MAT file.
    m = loadmat(path)

    keys = [k for k in m.keys() if not k.startswith("__")]

    if dataset_name:
        if dataset_name not in m:
            raise RuntimeError(f"Dataset '{dataset_name}' not found. Available: {keys}")
        return m[dataset_name], dataset_name, keys

    for candidate in ["rxGridSSB", "dataSSB"]:
        if candidate in m:
            return m[candidate], candidate, keys

    raise RuntimeError(f"No rxGridSSB/dataSSB variable found. Available: {keys}")


def matlab_to_rxgrid(path, dataset_name):
    x, used_name, keys = load_matlab_array(path, dataset_name)

    if "rxGridSSB" in used_name:
        rx = orient_rx_grid(x)
    else:
        data = orient_data_ssb(x)
        rx = data[60:300, 1:5, :]

    return rx.astype(np.complex64), used_name, keys


def maybe_normalize(x, mode):
    if mode == "none":
        return x

    if mode == "median":
        med = np.median(np.abs(x))
        if med > 0:
            return x / med

    return x


def stats_for(x):
    amp = np.abs(x)
    amp_db = 20 * np.log10(amp + 1e-9)
    phase = np.angle(x)

    z = np.exp(1j * phase.reshape(-1))
    mean_z = np.mean(z)

    return {
        "shape": list(x.shape),
        "n_values": int(x.size),
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
            "circular_mean_rad": float(np.angle(mean_z)),
            "resultant_length": float(np.abs(mean_z)),
            "linear_std": float(np.std(phase)),
        },
    }


def main():
    args = parse_args()

    python_path = Path(args.python_h5)
    matlab_path = Path(args.matlab_file)

    rx_py_raw = load_python_rxgrid(python_path)
    rx_mat_raw, matlab_used_name, matlab_keys = matlab_to_rxgrid(matlab_path, args.matlab_dataset)

    rx_py = maybe_normalize(rx_py_raw, args.normalize)
    rx_mat = maybe_normalize(rx_mat_raw, args.normalize)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    s_py = stats_for(rx_py)
    s_mat = stats_for(rx_mat)

    summary = {
        "python_h5": str(python_path),
        "matlab_file": str(matlab_path),
        "matlab_dataset_used": matlab_used_name,
        "normalize": args.normalize,
        "python": s_py,
        "matlab": s_mat,
        "ratios_python_over_matlab": {
            "amplitude_mean": s_py["amplitude"]["mean"] / s_mat["amplitude"]["mean"],
            "amplitude_median": s_py["amplitude"]["median"] / s_mat["amplitude"]["median"],
        },
        "matlab_available_keys": matlab_keys,
    }

    (out_dir / "comparison_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("=== Python vs MATLAB rxGridSSB comparison ===")
    print(f"Python shape:       {rx_py.shape}")
    print(f"MATLAB shape:       {rx_mat.shape}")
    print(f"MATLAB dataset:     {matlab_used_name}")
    print(f"Normalize:          {args.normalize}")
    print(f"Summary:            {out_dir / 'comparison_summary.json'}")

    print("\nAmplitude mean:")
    print(f"  Python: {s_py['amplitude']['mean']:.6f}")
    print(f"  MATLAB: {s_mat['amplitude']['mean']:.6f}")
    print(f"  ratio:  {summary['ratios_python_over_matlab']['amplitude_mean']:.6f}")

    print("\nAmplitude median:")
    print(f"  Python: {s_py['amplitude']['median']:.6f}")
    print(f"  MATLAB: {s_mat['amplitude']['median']:.6f}")
    print(f"  ratio:  {summary['ratios_python_over_matlab']['amplitude_median']:.6f}")

    amp_py_db = 20 * np.log10(np.abs(rx_py).reshape(-1) + 1e-9)
    amp_mat_db = 20 * np.log10(np.abs(rx_mat).reshape(-1) + 1e-9)

    plt.figure(figsize=(8, 4))
    plt.hist(amp_py_db, bins=120, density=True, alpha=0.5, label=args.label_python)
    plt.hist(amp_mat_db, bins=120, density=True, alpha=0.5, label=args.label_matlab)
    plt.xlabel("20log10(|rxGridSSB|)")
    plt.ylabel("Density")
    plt.title("Amplitude distribution comparison [dB]")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_hist_amplitude_db.png", dpi=160)
    plt.close()

    phase_py = np.angle(rx_py).reshape(-1)
    phase_mat = np.angle(rx_mat).reshape(-1)

    plt.figure(figsize=(8, 4))
    plt.hist(phase_py, bins=120, range=(-np.pi, np.pi), density=True, alpha=0.5, label=args.label_python)
    plt.hist(phase_mat, bins=120, range=(-np.pi, np.pi), density=True, alpha=0.5, label=args.label_matlab)
    plt.xlabel("Phase [rad]")
    plt.ylabel("Density")
    plt.title("Phase distribution comparison")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_hist_phase.png", dpi=160)
    plt.close()

    mean_sc_py = np.mean(np.abs(rx_py), axis=(1, 2))
    mean_sc_mat = np.mean(np.abs(rx_mat), axis=(1, 2))

    plt.figure(figsize=(9, 4))
    plt.plot(mean_sc_py, label=args.label_python)
    plt.plot(mean_sc_mat, label=args.label_matlab)
    plt.xlabel("Subcarrier index")
    plt.ylabel("Mean |rxGridSSB|")
    plt.title("Mean amplitude by subcarrier")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_mean_amplitude_by_subcarrier.png", dpi=160)
    plt.close()

    mean_sym_py = np.mean(np.abs(rx_py), axis=(0, 2))
    mean_sym_mat = np.mean(np.abs(rx_mat), axis=(0, 2))

    plt.figure(figsize=(7, 4))
    plt.plot(np.arange(4), mean_sym_py, marker="o", label=args.label_python)
    plt.plot(np.arange(4), mean_sym_mat, marker="o", label=args.label_matlab)
    plt.xlabel("OFDM symbol index")
    plt.ylabel("Mean |rxGridSSB|")
    plt.title("Mean amplitude by OFDM symbol")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "compare_mean_amplitude_by_symbol.png", dpi=160)
    plt.close()

    print("\nGenerated figures:")
    for p in sorted(out_dir.glob("*.png")):
        print(f"  {p}")


if __name__ == "__main__":
    main()
