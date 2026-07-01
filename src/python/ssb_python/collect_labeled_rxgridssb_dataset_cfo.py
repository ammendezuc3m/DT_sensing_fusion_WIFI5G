#!/usr/bin/env python3
"""
Collect a labeled Python 5G SSB dataset using the UHD/CFO/rxGridSSB pipeline.

This script is designed for operator-friendly dataset collection.

Workflow:
    1. The operator selects the label/scene/person/orientation.
    2. The script gives a preparation countdown.
    3. The script performs CFO warmup.
    4. The script captures live IQ blocks from the USRP B210.
    5. Each block is CFO-corrected, synchronized, and OFDM-demodulated.
    6. Valid dataSSB/rxGridSSB samples are saved to an H5 dataset.
    7. Metadata and a CSV capture log are saved next to the H5 file.

Output folder:
    data/python_ssb_datasets/<label>/<session_id>/

Main output:
    session_data.h5
    metadata.json
    capture_log.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from cfo_utils import apply_frequency_correction  # noqa: E402
from capture_online_rxgridssb_dataset_cfo import estimate_cfo_warmup  # noqa: E402
from profile_online_datassb_pipeline import (  # noqa: E402
    capture_one_block,
    configure_usrp,
    extract_rxgrid_from_waveform,
    make_rx_streamer,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Collect a labeled CFO-corrected Python rxGridSSB dataset."
    )

    # Dataset metadata.
    p.add_argument("--label", required=True, help="Class label, for example empty, P1, P2, P5.")
    p.add_argument("--scene", default="static", help="Scene or movement state, for example static, walking.")
    p.add_argument("--person-id", default="none", help="Person identifier, for example person_1 or none.")
    p.add_argument("--orientation", default="none", help="Target orientation, for example front, sideways, none.")
    p.add_argument("--notes", default="", help="Free-text notes stored in metadata.json.")
    p.add_argument("--output-root", default="data/python_ssb_datasets")

    # Operator timing.
    p.add_argument("--prep-sec", type=int, default=10, help="Preparation countdown before CFO warmup.")
    p.add_argument("--duration-sec", type=float, default=30.0, help="Collection duration after CFO warmup.")
    p.add_argument("--max-valid-samples", type=int, default=0, help="0 means no valid-sample limit.")
    p.add_argument("--progress-every", type=int, default=10)

    # USRP/capture.
    p.add_argument("--serial", default="34B73C3")
    p.add_argument("--freq", type=float, default=3541.44e6)
    p.add_argument("--rate", type=float, default=15.36e6)
    p.add_argument("--gain", type=float, default=60.0)
    p.add_argument("--duration-ms", type=float, default=20.0)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="")
    p.add_argument("--settle-sec", type=float, default=0.5)

    # DSP.
    p.add_argument("--nfft", type=int, default=512)
    p.add_argument("--demod-rb", type=int, default=30)
    p.add_argument("--nrb-ssb", type=int, default=20)
    p.add_argument("--num-symbols", type=int, default=6)
    p.add_argument("--force-nid2", type=int, default=0, choices=[0, 1, 2])
    p.add_argument("--min-pss-metric", type=float, default=0.50)

    # CFO.
    p.add_argument("--enable-cfo-correction", action="store_true", default=True)
    p.add_argument("--disable-cfo-correction", action="store_true")
    p.add_argument("--manual-cfo-hz", type=float, default=None)
    p.add_argument("--cfo-warmup-iters", type=int, default=30)
    p.add_argument("--cfo-correction-sign", type=float, default=-1.0)
    p.add_argument("--max-cfo-abs-hz", type=float, default=30000.0)

    return p.parse_args()


def now_utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(text: str) -> str:
    allowed = []
    for ch in str(text):
        if ch.isalnum() or ch in ["-", "_"]:
            allowed.append(ch)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "unknown"


def countdown(seconds: int, label: str) -> None:
    if seconds <= 0:
        return

    print("\n=== Preparation countdown ===")
    print(f"Target label: {label}")
    print(f"Collection starts after {seconds} seconds.")
    print("Move to the desired position now.\n")

    for remaining in range(seconds, 0, -1):
        print(f"Starting in {remaining:02d} s", end="\r", flush=True)
        time.sleep(1.0)

    print("Starting now.                 ")


def create_session_dir(args: argparse.Namespace) -> tuple[str, Path]:
    session_id = (
        f"session_{now_utc_compact()}_"
        f"{safe_name(args.label)}_"
        f"{safe_name(args.scene)}"
    )
    session_dir = Path(args.output_root) / safe_name(args.label) / session_id
    session_dir.mkdir(parents=True, exist_ok=False)
    return session_id, session_dir


def json_default(obj):
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def write_metadata(path: Path, metadata: dict) -> None:
    path.write_text(json.dumps(metadata, indent=2, default=json_default), encoding="utf-8")


def main() -> None:
    args = parse_args()

    if args.disable_cfo_correction:
        args.enable_cfo_correction = False

    session_id, session_dir = create_session_dir(args)

    h5_path = session_dir / "session_data.h5"
    metadata_path = session_dir / "metadata.json"
    log_path = session_dir / "capture_log.csv"

    metadata = {
        "schema_version": "python_5g_ssb_dataset_v1",
        "created_utc": now_utc_iso(),
        "session_id": session_id,
        "label": args.label,
        "scene": args.scene,
        "person_id": args.person_id,
        "orientation": args.orientation,
        "notes": args.notes,
        "output_files": {
            "h5": str(h5_path),
            "metadata_json": str(metadata_path),
            "capture_log_csv": str(log_path),
        },
        "usrp": {
            "serial": args.serial,
            "freq_hz": args.freq,
            "rate_sps": args.rate,
            "gain_db": args.gain,
            "duration_ms": args.duration_ms,
            "channel": args.channel,
            "antenna": args.antenna,
        },
        "dsp": {
            "nfft": args.nfft,
            "demod_rb": args.demod_rb,
            "nrb_ssb": args.nrb_ssb,
            "num_symbols": args.num_symbols,
            "force_nid2": args.force_nid2,
            "min_pss_metric": args.min_pss_metric,
        },
        "cfo": {
            "enabled": bool(args.enable_cfo_correction or args.manual_cfo_hz is not None),
            "manual_cfo_hz": args.manual_cfo_hz,
            "warmup_iters": args.cfo_warmup_iters,
            "correction_sign": args.cfo_correction_sign,
            "max_cfo_abs_hz": args.max_cfo_abs_hz,
        },
        "collection": {
            "prep_sec": args.prep_sec,
            "duration_sec": args.duration_sec,
            "max_valid_samples": args.max_valid_samples,
        },
    }

    write_metadata(metadata_path, metadata)

    print("=== Python 5G SSB labeled dataset collection ===")
    print(f"session id:      {session_id}")
    print(f"label:           {args.label}")
    print(f"scene:           {args.scene}")
    print(f"person id:       {args.person_id}")
    print(f"orientation:     {args.orientation}")
    print(f"session dir:     {session_dir}")
    print(f"h5 output:       {h5_path}")
    print(f"metadata:        {metadata_path}")
    print(f"capture log:     {log_path}")

    countdown(args.prep_sec, args.label)

    usrp = configure_usrp(args)
    actual_rate = float(usrp.get_rx_rate(args.channel))
    samples_per_block = int(round(actual_rate * args.duration_ms * 1e-3))
    rx_streamer = make_rx_streamer(usrp, args.channel)
    max_samps = rx_streamer.get_max_num_samps()

    if args.manual_cfo_hz is not None:
        cfo_hz = float(args.manual_cfo_hz)
        print(f"\nUsing manual CFO: {cfo_hz:.3f} Hz")
    elif args.enable_cfo_correction:
        cfo_hz, _cfo_rows = estimate_cfo_warmup(
            rx_streamer=rx_streamer,
            samples_per_block=samples_per_block,
            max_samps=max_samps,
            sample_rate=actual_rate,
            args=args,
        )
    else:
        cfo_hz = 0.0
        print("\nCFO correction disabled.")

    metadata["cfo"]["cfo_hz_applied"] = cfo_hz
    metadata["usrp"]["actual_rate_sps"] = actual_rate
    metadata["usrp"]["samples_per_block"] = samples_per_block
    write_metadata(metadata_path, metadata)

    print("\n=== Collection loop ===")
    print(f"duration:         {args.duration_sec:.1f} s")
    print(f"max valid:        {'unlimited' if args.max_valid_samples == 0 else args.max_valid_samples}")
    print(f"CFO applied:      {cfo_hz:.3f} Hz")
    print(f"samples/block:    {samples_per_block}")
    print("Press Ctrl+C to stop early.\n")

    data_ssb_list = []
    rx_grid_list = []

    rows = []
    fieldnames = [
        "attempt_index",
        "accepted_index",
        "timestamp_utc",
        "valid",
        "label",
        "scene",
        "person_id",
        "orientation",
        "pss_metric",
        "nid2",
        "timing_offset_samples",
        "timing_offset_ms",
        "n_symbols_extracted",
        "rxGridSSB_mean_abs",
        "rxGridSSB_max_abs",
        "capture_time_ms",
        "pss_time_ms",
        "ofdm_time_ms",
        "dsp_time_ms",
        "loop_time_ms",
        "cfo_hz_applied",
        "error",
    ]

    start = time.perf_counter()
    attempt_idx = 0
    accepted_idx = 0

    try:
        while True:
            elapsed = time.perf_counter() - start

            if elapsed >= args.duration_sec:
                break

            if args.max_valid_samples > 0 and accepted_idx >= args.max_valid_samples:
                break

            loop_t0 = time.perf_counter()
            error = ""
            valid = False
            data_ssb = None
            rx_grid_ssb = None
            timing_info = {}
            timing_breakdown = {}
            capture_time_ms = np.nan

            try:
                cap_t0 = time.perf_counter()
                waveform = capture_one_block(
                    rx_streamer=rx_streamer,
                    total_samples=samples_per_block,
                    max_samps=max_samps,
                )
                cap_t1 = time.perf_counter()
                capture_time_ms = 1000.0 * (cap_t1 - cap_t0)

                if args.enable_cfo_correction or args.manual_cfo_hz is not None:
                    waveform = apply_frequency_correction(
                        waveform=waveform,
                        cfo_hz=cfo_hz,
                        sample_rate=actual_rate,
                        sign=args.cfo_correction_sign,
                    )

                data_ssb, rx_grid_ssb, timing_info, timing_breakdown = extract_rxgrid_from_waveform(
                    waveform=waveform,
                    args=args,
                )

                metric = float(timing_info.get("metric", 0.0))
                n_symbols = int(timing_info.get("n_symbols_extracted", 0))

                valid = bool(
                    metric >= args.min_pss_metric
                    and n_symbols == args.num_symbols
                    and data_ssb.shape == (360, 6)
                    and rx_grid_ssb.shape == (240, 4)
                )

                if valid:
                    data_ssb_list.append(data_ssb.astype(np.complex64))
                    rx_grid_list.append(rx_grid_ssb.astype(np.complex64))
                    accepted_idx += 1

            except Exception as exc:
                error = str(exc)
                valid = False

            loop_t1 = time.perf_counter()
            loop_time_ms = 1000.0 * (loop_t1 - loop_t0)

            rx_mean = float(np.mean(np.abs(rx_grid_ssb))) if rx_grid_ssb is not None else np.nan
            rx_max = float(np.max(np.abs(rx_grid_ssb))) if rx_grid_ssb is not None else np.nan

            row = {
                "attempt_index": attempt_idx,
                "accepted_index": accepted_idx - 1 if valid else "",
                "timestamp_utc": now_utc_iso(),
                "valid": int(valid),
                "label": args.label,
                "scene": args.scene,
                "person_id": args.person_id,
                "orientation": args.orientation,
                "pss_metric": timing_info.get("metric", np.nan),
                "nid2": timing_info.get("nid2", -1),
                "timing_offset_samples": timing_info.get("timing_offset_samples", -1),
                "timing_offset_ms": timing_info.get("timing_offset_ms", np.nan),
                "n_symbols_extracted": timing_info.get("n_symbols_extracted", 0),
                "rxGridSSB_mean_abs": rx_mean,
                "rxGridSSB_max_abs": rx_max,
                "capture_time_ms": capture_time_ms,
                "pss_time_ms": timing_breakdown.get("pss_time_ms", np.nan),
                "ofdm_time_ms": timing_breakdown.get("ofdm_time_ms", np.nan),
                "dsp_time_ms": timing_breakdown.get("total_dsp_time_ms", np.nan),
                "loop_time_ms": loop_time_ms,
                "cfo_hz_applied": cfo_hz,
                "error": error,
            }
            rows.append(row)

            if args.progress_every > 0 and (attempt_idx % args.progress_every == 0 or valid):
                print(
                    f"[attempt={attempt_idx:05d} accepted={accepted_idx:05d}] "
                    f"valid={int(valid)} "
                    f"pss={float(row['pss_metric']) if np.isfinite(row['pss_metric']) else float('nan'):.3f} "
                    f"rx_mean={rx_mean if np.isfinite(rx_mean) else float('nan'):.3f} "
                    f"loop={loop_time_ms:.2f} ms "
                    f"err={error}"
                )

            attempt_idx += 1

    except KeyboardInterrupt:
        print("\nInterrupted by user. Saving collected samples...")

    print("\n=== Saving dataset ===")

    if data_ssb_list:
        data_ssb_arr = np.stack(data_ssb_list, axis=2).astype(np.complex64)
        rx_grid_arr = np.stack(rx_grid_list, axis=2).astype(np.complex64)
    else:
        data_ssb_arr = np.zeros((360, 6, 0), dtype=np.complex64)
        rx_grid_arr = np.zeros((240, 4, 0), dtype=np.complex64)

    accepted_rows = [r for r in rows if int(r["valid"]) == 1]

    def arr_from_rows(key: str, dtype=np.float32):
        return np.asarray([r[key] for r in accepted_rows], dtype=dtype)

    with h5py.File(h5_path, "w") as f:
        f.create_dataset("dataSSB", data=data_ssb_arr)
        f.create_dataset("rxGridSSB", data=rx_grid_arr)

        f.create_dataset("pss_metric", data=arr_from_rows("pss_metric", np.float32))
        f.create_dataset("timing_offset_samples", data=arr_from_rows("timing_offset_samples", np.int64))
        f.create_dataset("timing_offset_ms", data=arr_from_rows("timing_offset_ms", np.float32))
        f.create_dataset("capture_time_ms", data=arr_from_rows("capture_time_ms", np.float32))
        f.create_dataset("pss_time_ms", data=arr_from_rows("pss_time_ms", np.float32))
        f.create_dataset("ofdm_time_ms", data=arr_from_rows("ofdm_time_ms", np.float32))
        f.create_dataset("dsp_time_ms", data=arr_from_rows("dsp_time_ms", np.float32))
        f.create_dataset("loop_time_ms", data=arr_from_rows("loop_time_ms", np.float32))

        f.attrs["schema_version"] = "python_5g_ssb_dataset_v1"
        f.attrs["session_id"] = session_id
        f.attrs["label"] = args.label
        f.attrs["scene"] = args.scene
        f.attrs["person_id"] = args.person_id
        f.attrs["orientation"] = args.orientation
        f.attrs["created_utc"] = metadata["created_utc"]
        f.attrs["serial"] = args.serial
        f.attrs["freq_hz"] = args.freq
        f.attrs["rate_sps_requested"] = args.rate
        f.attrs["rate_sps_actual"] = actual_rate
        f.attrs["gain_db"] = args.gain
        f.attrs["duration_ms"] = args.duration_ms
        f.attrs["channel"] = args.channel
        f.attrs["force_nid2"] = args.force_nid2
        f.attrs["cfo_enabled"] = bool(args.enable_cfo_correction or args.manual_cfo_hz is not None)
        f.attrs["cfo_hz_applied"] = cfo_hz
        f.attrs["cfo_correction_sign"] = args.cfo_correction_sign
        f.attrs["num_attempts"] = len(rows)
        f.attrs["num_valid_samples"] = accepted_idx

    with log_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    metadata["summary"] = {
        "num_attempts": len(rows),
        "num_valid_samples": accepted_idx,
        "acceptance_rate": float(accepted_idx / len(rows)) if rows else 0.0,
        "h5_shapes": {
            "dataSSB": list(data_ssb_arr.shape),
            "rxGridSSB": list(rx_grid_arr.shape),
        },
    }
    write_metadata(metadata_path, metadata)

    print(f"H5 saved:          {h5_path}")
    print(f"metadata saved:    {metadata_path}")
    print(f"capture log saved: {log_path}")
    print(f"attempts:          {len(rows)}")
    print(f"valid samples:     {accepted_idx}")
    print(f"acceptance rate:   {metadata['summary']['acceptance_rate']:.3f}")
    print(f"dataSSB shape:     {data_ssb_arr.shape}")
    print(f"rxGridSSB shape:   {rx_grid_arr.shape}")

    if accepted_idx == 0:
        print("\nWARNING: no valid samples were saved.")


if __name__ == "__main__":
    main()
