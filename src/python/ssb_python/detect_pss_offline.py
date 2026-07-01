#!/usr/bin/env python3
"""
Offline NR PSS/NID2 timing detector for raw IQ captures.

Input:
    .npz from test_capture_iq_uhd.py
    or .h5 from capture_iq_blocks_uhd.py

Goal:
    Detect approximate PSS timing and NID2 from raw time-domain IQ.

This is the first DSP step toward replacing MATLAB:
    mySSBurstFrequencyCorrectFast(...)
    nrTimingEstimate(...)

Current assumptions:
    SCS = 30 kHz
    Fs  = 15.36 Msps
    NFFT = 512
    SSB grid = 20 RB = 240 subcarriers
    PSS length = 127 subcarriers
    PSS is placed in OFDM symbol 2 of the timing reference, equivalent to:
        refGridTim = zeros([240, 2])
        refGridTim(nrPSSIndices, 2) = nrPSS(NID2)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import fftconvolve


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline NR PSS/NID2 detector from raw IQ capture.")

    p.add_argument("--input", required=True, help="Input .npz or .h5 IQ capture.")
    p.add_argument("--block", type=int, default=0, help="Block index for HDF5 input.")
    p.add_argument("--sample-rate", type=float, default=15.36e6, help="Sample rate in Hz.")
    p.add_argument("--scs", type=float, default=30e3, help="Subcarrier spacing in Hz.")
    p.add_argument("--nfft", type=int, default=512, help="OFDM FFT size.")
    p.add_argument("--nrb-ssb", type=int, default=20, help="SSB timing reference bandwidth in RB.")
    p.add_argument("--out-dir", default="results/python_pss_detection", help="Output directory.")
    p.add_argument("--top-k", type=int, default=10, help="Number of strongest peaks to print.")
    p.add_argument("--max-samples-plot", type=int, default=80000, help="Max samples in correlation plot.")

    return p.parse_args()


def load_waveform(path: Path, block: int) -> tuple[np.ndarray, dict]:
    if path.suffix.lower() == ".npz":
        z = np.load(path, allow_pickle=False)
        w = z["waveform"].astype(np.complex64)
        cfg = {}
        if "cfg_json" in z:
            cfg = json.loads(str(z["cfg_json"]))
        return w, cfg

    if path.suffix.lower() in [".h5", ".hdf5"]:
        with h5py.File(path, "r") as f:
            ds = f["waveform"]
            if ds.ndim == 1:
                w = ds[:].astype(np.complex64)
            else:
                w = ds[block, :].astype(np.complex64)

            cfg = {}
            if "cfg_json" in f.attrs:
                cfg = json.loads(f.attrs["cfg_json"])
            if "summary_json" in f.attrs:
                cfg["capture_summary"] = json.loads(f.attrs["summary_json"])

        return w, cfg

    raise ValueError(f"Unsupported input extension: {path.suffix}")


def generate_nr_pss(nid2: int) -> np.ndarray:
    """
    Generate NR PSS sequence for NID2 in {0,1,2}.

    d(n) = 1 - 2*x(m)
    m = (n + 43*NID2) mod 127

    x is a length-127 m-sequence generated with:
    x(i+7) = (x(i+4) + x(i)) mod 2
    initial x(0:6) = [0, 1, 1, 0, 1, 1, 1]
    """
    if nid2 not in (0, 1, 2):
        raise ValueError("NID2 must be 0, 1 or 2.")

    x = np.zeros(127, dtype=np.int8)
    x[:7] = np.array([0, 1, 1, 0, 1, 1, 1], dtype=np.int8)

    for i in range(120):
        x[i + 7] = (x[i + 4] + x[i]) % 2

    n = np.arange(127)
    m = (n + 43 * nid2) % 127
    d = 1 - 2 * x[m]

    return d.astype(np.complex64)


def build_pss_reference_grid(nid2: int, nrb_ssb: int = 20) -> np.ndarray:
    """
    Build a 240 x 2 timing reference grid.

    Equivalent conceptually to MATLAB:
        refGridTim = zeros([cfg.NRBSSB * 12, 2])
        refGridTim(nrPSSIndices, 2) = nrPSS(NID2)

    Python shape:
        [subcarrier, symbol]
    """
    n_sc = nrb_ssb * 12
    grid = np.zeros((n_sc, 2), dtype=np.complex64)

    pss = generate_nr_pss(nid2)

    start = (n_sc - len(pss)) // 2
    grid[start : start + len(pss), 1] = pss

    return grid


def ofdm_modulate_grid(
    grid: np.ndarray,
    nfft: int = 512,
    cp_lengths: list[int] | None = None,
) -> np.ndarray:
    """
    OFDM-modulate a baseband grid centered in an NFFT grid.

    grid shape:
        [n_subcarriers, n_symbols]

    Uses numpy ifft convention.
    """
    if cp_lengths is None:
        # For Fs=15.36 Msps, SCS=30 kHz, NFFT=512.
        # In a 30 kHz slot, CP lengths are commonly 40 for symbols 0 and 7,
        # and 36 for the other symbols. Here we only need first two symbols.
        cp_lengths = [40, 36]

    n_sc, n_sym = grid.shape

    if len(cp_lengths) < n_sym:
        raise ValueError("Not enough CP lengths for number of OFDM symbols.")

    start = nfft // 2 - n_sc // 2

    waveform_parts = []

    for sym in range(n_sym):
        freq_grid = np.zeros(nfft, dtype=np.complex64)
        freq_grid[start : start + n_sc] = grid[:, sym]

        # Map centered spectrum to IFFT order.
        time_symbol = np.fft.ifft(np.fft.ifftshift(freq_grid)).astype(np.complex64)

        cp = cp_lengths[sym]
        with_cp = np.concatenate([time_symbol[-cp:], time_symbol])

        waveform_parts.append(with_cp)

    return np.concatenate(waveform_parts).astype(np.complex64)


def correlate_reference(waveform: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """
    Sliding correlation magnitude.

    corr[k] roughly measures how well reference matches waveform[k:k+len(reference)].
    """
    ref = np.conj(reference[::-1])
    corr = fftconvolve(waveform, ref, mode="valid")
    metric = np.abs(corr) ** 2

    ref_energy = np.sum(np.abs(reference) ** 2) + 1e-12

    # Local waveform energy for normalization.
    wf_power = np.abs(waveform) ** 2
    win = np.ones(len(reference), dtype=np.float32)
    local_energy = fftconvolve(wf_power, win, mode="valid") + 1e-12

    metric_norm = metric / (ref_energy * local_energy)

    return metric_norm.astype(np.float32)


def strongest_peaks(metric: np.ndarray, top_k: int, guard: int) -> list[tuple[int, float]]:
    """
    Simple greedy top-K peak picker with exclusion guard around selected peaks.
    """
    work = metric.copy()
    peaks = []

    for _ in range(top_k):
        idx = int(np.argmax(work))
        val = float(work[idx])

        if not np.isfinite(val) or val <= 0:
            break

        peaks.append((idx, val))

        lo = max(0, idx - guard)
        hi = min(len(work), idx + guard + 1)
        work[lo:hi] = 0

    return peaks


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    waveform, cfg = load_waveform(input_path, args.block)

    out_dir = Path(args.out_dir) / f"{input_path.stem}_block{args.block}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== Offline PSS detection ===")
    print(f"input:        {input_path}")
    print(f"block:        {args.block}")
    print(f"samples:      {len(waveform)}")
    print(f"sample rate:  {args.sample_rate:.3f} Hz")
    print(f"SCS:          {args.scs:.3f} Hz")
    print(f"NFFT:         {args.nfft}")

    all_results = []

    plt.figure(figsize=(12, 5))

    max_plot = None

    for nid2 in [0, 1, 2]:
        grid = build_pss_reference_grid(nid2=nid2, nrb_ssb=args.nrb_ssb)
        ref = ofdm_modulate_grid(grid, nfft=args.nfft)

        metric = correlate_reference(waveform, ref)

        peaks = strongest_peaks(metric, top_k=args.top_k, guard=len(ref))

        best_idx, best_val = peaks[0]

        all_results.append(
            {
                "nid2": nid2,
                "reference_len_samples": int(len(ref)),
                "best_timing_offset_samples": int(best_idx),
                "best_metric": float(best_val),
                "peaks": [
                    {
                        "timing_offset_samples": int(i),
                        "metric": float(v),
                        "time_ms": float(1000 * i / args.sample_rate),
                    }
                    for i, v in peaks
                ],
            }
        )

        print(f"\nNID2={nid2}")
        print(f"  reference length: {len(ref)} samples")
        print(f"  best timing:      {best_idx} samples = {1000*best_idx/args.sample_rate:.4f} ms")
        print(f"  best metric:      {best_val:.6e}")

        for rank, (idx, val) in enumerate(peaks[:5], start=1):
            print(f"    peak {rank:02d}: idx={idx:8d} time={1000*idx/args.sample_rate:8.4f} ms metric={val:.6e}")

        n_plot = min(args.max_samples_plot, len(metric))
        if max_plot is None:
            max_plot = n_plot

        x_ms = 1000 * np.arange(n_plot) / args.sample_rate
        plt.plot(x_ms, metric[:n_plot], label=f"NID2={nid2}")

    best = max(all_results, key=lambda d: d["best_metric"])

    summary = {
        "input": str(input_path),
        "block": args.block,
        "sample_rate": args.sample_rate,
        "scs": args.scs,
        "nfft": args.nfft,
        "nrb_ssb": args.nrb_ssb,
        "best_nid2": int(best["nid2"]),
        "best_timing_offset_samples": int(best["best_timing_offset_samples"]),
        "best_timing_offset_ms": float(1000 * best["best_timing_offset_samples"] / args.sample_rate),
        "best_metric": float(best["best_metric"]),
        "results": all_results,
        "cfg": cfg,
    }

    summary_path = out_dir / "pss_detection_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    plt.xlabel("Time [ms]")
    plt.ylabel("Normalized correlation metric")
    plt.title("NR PSS timing correlation")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "pss_correlation.png", dpi=160)
    plt.close()

    print("\n=== Best result ===")
    print(f"NID2:          {summary['best_nid2']}")
    print(f"timing:        {summary['best_timing_offset_samples']} samples")
    print(f"timing [ms]:   {summary['best_timing_offset_ms']:.6f}")
    print(f"metric:        {summary['best_metric']:.6e}")
    print(f"summary:       {summary_path}")
    print(f"figure:        {out_dir / 'pss_correlation.png'}")


if __name__ == "__main__":
    main()
