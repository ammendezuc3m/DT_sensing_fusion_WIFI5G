#!/usr/bin/env python3
"""
Live Python UHD -> PSS -> OFDM -> dataSSB/rxGridSSB dataset capture.

This script captures live 20 ms IQ blocks, extracts dataSSB/rxGridSSB, and saves
the processed grids only.

Output:
    dataSSB        = 360 x 6 x N complex64
    rxGridSSB      = 240 x 4 x N complex64
    timing_offsets = N
    pss_metrics    = N
    nid2           = N
    valid_mask     = N
    timing metrics = N
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import h5py
import numpy as np

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from profile_online_datassb_pipeline import (  # noqa: E402
    configure_usrp,
    make_rx_streamer,
    capture_one_block,
    extract_rxgrid_from_waveform,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Capture live rxGridSSB dataset using Python UHD pipeline.")

    p.add_argument("--serial", default="34B73C3")
    p.add_argument("--freq", type=float, default=3541.44e6)
    p.add_argument("--rate", type=float, default=15.36e6)
    p.add_argument("--gain", type=float, default=60.0)
    p.add_argument("--duration-ms", type=float, default=20.0)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="")
    p.add_argument("--settle-sec", type=float, default=0.5)

    p.add_argument("--num-iters", type=int, default=200)
    p.add_argument("--warmup-iters", type=int, default=10)
    p.add_argument("--progress-every", type=int, default=10)

    p.add_argument("--nfft", type=int, default=512)
    p.add_argument("--demod-rb", type=int, default=30)
    p.add_argument("--nrb-ssb", type=int, default=20)
    p.add_argument("--num-symbols", type=int, default=6)
    p.add_argument("--force-nid2", type=int, default=0, choices=[0, 1, 2])
    p.add_argument("--min-pss-metric", type=float, default=0.50)

    p.add_argument("--out-dir", default="results/python_online_rxgridssb_dataset")
    p.add_argument("--prefix", default="python_online_rxgridssb")

    return p.parse_args()


def main() -> None:
    args = parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_h5 = out_dir / f"{args.prefix}_{timestamp}.h5"
    out_csv = out_dir / f"{args.prefix}_{timestamp}_metadata.csv"
    out_summary = out_dir / f"{args.prefix}_{timestamp}_summary.json"

    usrp = configure_usrp(args)
    ch = args.channel
    actual_rate = float(usrp.get_rx_rate(ch))
    samples_per_block = int(round(actual_rate * args.duration_ms * 1e-3))

    rx_streamer = make_rx_streamer(usrp, ch)
    max_samps = rx_streamer.get_max_num_samps()

    n = args.num_iters

    data_all = np.zeros((args.demod_rb * 12, args.num_symbols, n), dtype=np.complex64)
    rx_all = np.zeros((240, 4, n), dtype=np.complex64)

    valid_mask = np.zeros(n, dtype=bool)
    nid2 = np.full(n, -1, dtype=np.int16)
    timing_offsets = np.full(n, -1, dtype=np.int64)
    pss_metrics = np.full(n, np.nan, dtype=np.float32)

    capture_time_ms = np.full(n, np.nan, dtype=np.float32)
    pss_time_ms = np.full(n, np.nan, dtype=np.float32)
    ofdm_time_ms = np.full(n, np.nan, dtype=np.float32)
    dsp_time_ms = np.full(n, np.nan, dtype=np.float32)
    loop_time_ms = np.full(n, np.nan, dtype=np.float32)

    rows = []

    print("=== Live rxGridSSB dataset capture ===")
    print(f"iterations:        {n}")
    print(f"warmup iterations: {args.warmup_iters}")
    print(f"samples/block:     {samples_per_block}")
    print(f"out h5:            {out_h5}")

    for i in range(n):
        loop_t0 = time.perf_counter()

        row = {
            "iter": i,
            "used_for_summary": int(i >= args.warmup_iters),
            "valid": 0,
            "nid2": -1,
            "timing_samples": -1,
            "timing_ms": np.nan,
            "pss_metric": np.nan,
            "capture_time_ms": np.nan,
            "pss_time_ms": np.nan,
            "ofdm_time_ms": np.nan,
            "dsp_time_ms": np.nan,
            "loop_time_ms": np.nan,
            "rxGridSSB_mean_abs": np.nan,
            "rxGridSSB_max_abs": np.nan,
            "error": "",
        }

        try:
            cap_t0 = time.perf_counter()
            waveform = capture_one_block(
                rx_streamer=rx_streamer,
                total_samples=samples_per_block,
                max_samps=max_samps,
            )
            cap_t1 = time.perf_counter()

            data_ssb, rx_grid_ssb, timing_info, tb = extract_rxgrid_from_waveform(
                waveform=waveform,
                args=args,
            )

            loop_t1 = time.perf_counter()

            metric = float(timing_info["metric"])
            timing = int(timing_info["timing_offset_samples"])
            n_symbols = int(timing_info["n_symbols_extracted"])

            valid = bool(
                metric >= args.min_pss_metric
                and n_symbols == args.num_symbols
                and data_ssb.shape == (args.demod_rb * 12, args.num_symbols)
                and rx_grid_ssb.shape == (240, 4)
            )

            data_all[:, :, i] = data_ssb
            rx_all[:, :, i] = rx_grid_ssb

            valid_mask[i] = valid
            nid2[i] = int(timing_info["nid2"])
            timing_offsets[i] = timing
            pss_metrics[i] = metric

            capture_time_ms[i] = 1000.0 * (cap_t1 - cap_t0)
            pss_time_ms[i] = tb["pss_time_ms"]
            ofdm_time_ms[i] = tb["ofdm_time_ms"]
            dsp_time_ms[i] = tb["total_dsp_time_ms"]
            loop_time_ms[i] = 1000.0 * (loop_t1 - loop_t0)

            row.update(
                {
                    "valid": int(valid),
                    "nid2": int(nid2[i]),
                    "timing_samples": int(timing_offsets[i]),
                    "timing_ms": float(1000.0 * timing_offsets[i] / actual_rate),
                    "pss_metric": float(pss_metrics[i]),
                    "capture_time_ms": float(capture_time_ms[i]),
                    "pss_time_ms": float(pss_time_ms[i]),
                    "ofdm_time_ms": float(ofdm_time_ms[i]),
                    "dsp_time_ms": float(dsp_time_ms[i]),
                    "loop_time_ms": float(loop_time_ms[i]),
                    "rxGridSSB_mean_abs": float(np.mean(np.abs(rx_grid_ssb))),
                    "rxGridSSB_max_abs": float(np.max(np.abs(rx_grid_ssb))),
                }
            )

        except Exception as exc:
            loop_t1 = time.perf_counter()
            loop_time_ms[i] = 1000.0 * (loop_t1 - loop_t0)
            row["loop_time_ms"] = float(loop_time_ms[i])
            row["error"] = str(exc)

        rows.append(row)

        if args.progress_every > 0 and (i % args.progress_every == 0 or i == n - 1):
            print(
                f"[{i + 1:04d}/{n:04d}] "
                f"valid={row['valid']} "
                f"metric={row['pss_metric']:.3f} "
                f"loop={row['loop_time_ms']:.2f} ms "
                f"rx_mean={row['rxGridSSB_mean_abs']:.3f} "
                f"err={row['error']}"
            )

    used_mask = np.arange(n) >= args.warmup_iters
    valid_summary_mask = valid_mask & used_mask

    def safe_stats(x: np.ndarray, mask: np.ndarray) -> dict:
        v = np.asarray(x[mask], dtype=np.float64)
        v = v[np.isfinite(v)]
        if v.size == 0:
            return {"mean": None, "median": None, "min": None, "max": None, "p95": None}
        return {
            "mean": float(np.mean(v)),
            "median": float(np.median(v)),
            "min": float(np.min(v)),
            "max": float(np.max(v)),
            "p95": float(np.percentile(v, 95)),
        }

    summary = {
        "config": {
            "serial": args.serial,
            "freq_hz": args.freq,
            "rate_hz_requested": args.rate,
            "rate_hz_actual": actual_rate,
            "gain_db": args.gain,
            "duration_ms": args.duration_ms,
            "samples_per_block": samples_per_block,
            "num_iters": args.num_iters,
            "warmup_iters": args.warmup_iters,
            "nfft": args.nfft,
            "demod_rb": args.demod_rb,
            "nrb_ssb": args.nrb_ssb,
            "num_symbols": args.num_symbols,
            "force_nid2": args.force_nid2,
            "min_pss_metric": args.min_pss_metric,
        },
        "shapes": {
            "dataSSB": list(data_all.shape),
            "rxGridSSB": list(rx_all.shape),
        },
        "counts": {
            "total_iters": int(n),
            "used_for_summary": int(np.sum(used_mask)),
            "valid_total": int(np.sum(valid_mask)),
            "valid_used_for_summary": int(np.sum(valid_summary_mask)),
            "valid_ratio_used_for_summary": float(np.mean(valid_mask[used_mask])),
        },
        "timing_valid_summary": {
            "capture_time_ms": safe_stats(capture_time_ms, valid_summary_mask),
            "pss_time_ms": safe_stats(pss_time_ms, valid_summary_mask),
            "ofdm_time_ms": safe_stats(ofdm_time_ms, valid_summary_mask),
            "dsp_time_ms": safe_stats(dsp_time_ms, valid_summary_mask),
            "loop_time_ms": safe_stats(loop_time_ms, valid_summary_mask),
        },
        "pss_metric_valid_summary": safe_stats(pss_metrics, valid_summary_mask),
        "rxGridSSB_valid_summary": {
            "mean_abs": float(np.mean(np.abs(rx_all[:, :, valid_summary_mask]))) if np.any(valid_summary_mask) else None,
            "max_abs": float(np.max(np.abs(rx_all[:, :, valid_summary_mask]))) if np.any(valid_summary_mask) else None,
        },
    }

    with h5py.File(out_h5, "w") as f:
        f.create_dataset("dataSSB", data=data_all)
        f.create_dataset("rxGridSSB", data=rx_all)
        f.create_dataset("valid_mask", data=valid_mask)
        f.create_dataset("nid2", data=nid2)
        f.create_dataset("timing_offsets", data=timing_offsets)
        f.create_dataset("pss_metrics", data=pss_metrics)
        f.create_dataset("capture_time_ms", data=capture_time_ms)
        f.create_dataset("pss_time_ms", data=pss_time_ms)
        f.create_dataset("ofdm_time_ms", data=ofdm_time_ms)
        f.create_dataset("dsp_time_ms", data=dsp_time_ms)
        f.create_dataset("loop_time_ms", data=loop_time_ms)
        f.attrs["summary_json"] = json.dumps(summary, indent=2)

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    out_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== Saved dataset ===")
    print(f"dataSSB shape:       {data_all.shape}")
    print(f"rxGridSSB shape:     {rx_all.shape}")
    print(f"valid used:          {int(np.sum(valid_summary_mask))}/{int(np.sum(used_mask))}")
    print(f"mean loop valid:     {summary['timing_valid_summary']['loop_time_ms']['mean']:.3f} ms")
    print(f"mean rxGridSSB abs:  {summary['rxGridSSB_valid_summary']['mean_abs']:.6f}")
    print(f"h5:                  {out_h5}")
    print(f"csv:                 {out_csv}")
    print(f"summary:             {out_summary}")


if __name__ == "__main__":
    main()
