#!/usr/bin/env python3
"""
Online 5G SSB rxGridSSB JSONL receiver.

Pipeline:
    USRP B210
      -> IQ capture
      -> optional CFO warmup/correction
      -> PSS/NID2/timing detection
      -> OFDM demodulation
      -> rxGridSSB extraction, shape (240, 4)
      -> append one JSON object per valid grid to JSONL

No inference, SCP or Digital Twin export is performed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from cfo_utils import apply_frequency_correction
from capture_online_rxgridssb_dataset_cfo import estimate_cfo_warmup
from profile_online_datassb_pipeline import (
    capture_one_block,
    configure_usrp,
    extract_rxgrid_from_waveform,
    make_rx_streamer,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Receive 5G SSB grids and append each valid rxGridSSB "
            "to a JSONL file."
        )
    )

    # USRP.
    parser.add_argument("--serial", required=True)
    parser.add_argument("--freq", type=float, default=3541.44e6)
    parser.add_argument("--rate", type=float, default=15.36e6)
    parser.add_argument("--gain", type=float, default=60.0)
    parser.add_argument("--duration-ms", type=float, default=20.0)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--antenna", default="")
    parser.add_argument("--settle-sec", type=float, default=0.5)

    # DSP.
    parser.add_argument("--nfft", type=int, default=512)
    parser.add_argument("--demod-rb", type=int, default=30)
    parser.add_argument("--nrb-ssb", type=int, default=20)
    parser.add_argument("--num-symbols", type=int, default=6)
    parser.add_argument(
        "--force-nid2",
        type=int,
        default=0,
        choices=[0, 1, 2],
    )
    parser.add_argument("--min-pss-metric", type=float, default=0.50)

    # CFO.
    parser.add_argument("--enable-cfo-correction", action="store_true")
    parser.add_argument("--manual-cfo-hz", type=float, default=None)
    parser.add_argument("--cfo-warmup-iters", type=int, default=30)
    parser.add_argument("--cfo-correction-sign", type=float, default=-1.0)
    parser.add_argument("--max-cfo-abs-hz", type=float, default=30000.0)

    # Loop/output.
    parser.add_argument(
        "--num-iters",
        type=int,
        default=0,
        help="0 means run until Ctrl+C.",
    )
    parser.add_argument("--warmup-iters", type=int, default=5)
    parser.add_argument("--progress-every", type=int, default=1)
    parser.add_argument(
        "--output-jsonl",
        default="results/online/rxgridssb_5g.jsonl",
    )
    parser.add_argument(
        "--truncate-output",
        action="store_true",
        help="Erase the existing JSONL when the program starts.",
    )
    parser.add_argument(
        "--write-invalid",
        action="store_true",
        help="Also append invalid observations without complex_features.",
    )

    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def complex_array_to_json(values: np.ndarray) -> list[dict[str, float]]:
    """
    Flatten a complex array in C order and serialize it as real/imag pairs.
    """
    flat = np.asarray(values, dtype=np.complex64).reshape(-1, order="C")

    return [
        {
            "real": float(value.real),
            "imag": float(value.imag),
        }
        for value in flat
    ]


def make_payload(
    *,
    iteration: int,
    valid: bool,
    rx_grid_ssb: np.ndarray | None,
    timing_info: dict,
    timing_breakdown: dict,
    center_frequency_hz: float,
    sample_rate_hz: float,
    gain_db: float,
    channel: int,
    serial: str,
    cfo_hz: float,
    cfo_enabled: bool,
    capture_time_ms: float,
    loop_time_ms: float,
    error: str,
) -> dict:
    grid_valid = (
        valid
        and rx_grid_ssb is not None
        and rx_grid_ssb.shape == (240, 4)
    )

    if grid_valid:
        grid = np.asarray(rx_grid_ssb, dtype=np.complex64)
        amplitude = np.abs(grid)

        complex_features = complex_array_to_json(grid)

        mean_abs = float(np.mean(amplitude))
        median_abs = float(np.median(amplitude))
        std_abs = float(np.std(amplitude))
        max_abs = float(np.max(amplitude))
        mean_power = float(np.mean(np.square(amplitude)))
        power_db = float(
            10.0 * np.log10(max(mean_power, np.finfo(np.float32).tiny))
        )
    else:
        complex_features = []
        mean_abs = None
        median_abs = None
        std_abs = None
        max_abs = None
        power_db = None

    timestamp_ns = time.time_ns()

    return {
        "protocol_version": 1,
        "schema": "5g_ssb_rxgrid_jsonl_v1",
        "waveform_type": "5g_ssb",
        "profile_id": "n78_ssb_30khz",

        "iteration": int(iteration),
        "valid": bool(grid_valid),
        "error": str(error),

        "rx_timestamp_ns": int(timestamp_ns),
        "timestamp_unix": float(timestamp_ns / 1.0e9),
        "timestamp_utc": now_iso(),
        "timestamp_semantics": "host_serialization_time_operational_only",

        "usrp": {
            "serial": str(serial),
            "channel": int(channel),
            "gain_db": float(gain_db),
        },

        "center_frequency_hz": float(center_frequency_hz),
        "sample_rate_hz": float(sample_rate_hz),
        "cfo_hz": float(cfo_hz),
        "cfo_correction_enabled": bool(cfo_enabled),

        "feature_name": "rxGridSSB",
        "feature_dtype": "complex64",
        "feature_shape": [240, 4],
        "feature_flatten_order": "C",
        "feature_count": 960,
        "complex_features": complex_features,

        "numeric_metadata": {
            "nid2": int(timing_info.get("nid2", -1)),
            "pss_metric": float(timing_info.get("metric", 0.0)),
            "timing_offset_samples": int(
                timing_info.get("timing_offset_samples", -1)
            ),
            "timing_offset_ms": float(
                timing_info.get("timing_offset_ms", -1.0)
            ),
            "n_symbols_extracted": int(
                timing_info.get("n_symbols_extracted", 0)
            ),
            "rxgrid_mean_abs": mean_abs,
            "rxgrid_median_abs": median_abs,
            "rxgrid_std_abs": std_abs,
            "rxgrid_max_abs": max_abs,
            "rxgrid_mean_power_db": power_db,
            "capture_time_ms": float(capture_time_ms),
            "pss_time_ms": float(
                timing_breakdown.get("pss_time_ms", 0.0)
            ),
            "ofdm_time_ms": float(
                timing_breakdown.get("ofdm_time_ms", 0.0)
            ),
            "dsp_time_ms": float(
                timing_breakdown.get("total_dsp_time_ms", 0.0)
            ),
            "loop_time_ms": float(loop_time_ms),
        },
    }


def main() -> None:
    args = parse_args()

    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_mode = "w" if args.truncate_output else "a"

    print("=== Online 5G SSB rxGridSSB JSONL receiver ===")
    print(f"USRP serial:        {args.serial}")
    print(f"requested freq:     {args.freq / 1e6:.6f} MHz")
    print(f"requested rate:     {args.rate / 1e6:.6f} Msps")
    print(f"requested gain:     {args.gain:.2f} dB")
    print(f"output JSONL:       {output_path}")
    print(f"output mode:        {'truncate' if args.truncate_output else 'append'}")
    print(
        "num iters:         "
        f"{'forever' if args.num_iters == 0 else args.num_iters}"
    )

    usrp = configure_usrp(args)

    actual_rate = float(usrp.get_rx_rate(args.channel))
    actual_frequency = float(usrp.get_rx_freq(args.channel))
    actual_gain = float(usrp.get_rx_gain(args.channel))

    samples_per_block = int(
        round(actual_rate * args.duration_ms * 1e-3)
    )

    rx_streamer = make_rx_streamer(usrp, args.channel)
    max_samples = rx_streamer.get_max_num_samps()

    if args.manual_cfo_hz is not None:
        cfo_hz = float(args.manual_cfo_hz)
        print(f"\nUsing manual CFO: {cfo_hz:.3f} Hz")
    elif args.enable_cfo_correction:
        cfo_hz, _rows = estimate_cfo_warmup(
            rx_streamer=rx_streamer,
            samples_per_block=samples_per_block,
            max_samps=max_samples,
            sample_rate=actual_rate,
            args=args,
        )
    else:
        cfo_hz = 0.0
        print("\nCFO correction disabled.")

    print("\n=== Online loop ===")
    print(f"actual rate:        {actual_rate:.3f} S/s")
    print(f"actual frequency:   {actual_frequency / 1e6:.6f} MHz")
    print(f"actual gain:        {actual_gain:.2f} dB")
    print(f"samples/block:      {samples_per_block}")
    print(f"CFO applied:        {cfo_hz:.3f} Hz")
    print("Ctrl+C to stop\n")

    frames_written = 0
    valid_frames = 0
    invalid_frames = 0

    with output_path.open(
        output_mode,
        encoding="utf-8",
        buffering=1,
    ) as output_file:
        iteration = 0

        try:
            while True:
                if (
                    args.num_iters > 0
                    and iteration >= args.num_iters
                ):
                    break

                loop_start = time.perf_counter()

                valid = False
                error = ""
                rx_grid_ssb = None
                timing_info: dict = {}
                timing_breakdown: dict = {}
                capture_time_ms = float("nan")

                try:
                    capture_start = time.perf_counter()

                    waveform = capture_one_block(
                        rx_streamer=rx_streamer,
                        total_samples=samples_per_block,
                        max_samps=max_samples,
                    )

                    capture_time_ms = (
                        1000.0
                        * (time.perf_counter() - capture_start)
                    )

                    if (
                        args.enable_cfo_correction
                        or args.manual_cfo_hz is not None
                    ):
                        waveform = apply_frequency_correction(
                            waveform=waveform,
                            cfo_hz=cfo_hz,
                            sample_rate=actual_rate,
                            sign=args.cfo_correction_sign,
                        )

                    (
                        _data_ssb,
                        rx_grid_ssb,
                        timing_info,
                        timing_breakdown,
                    ) = extract_rxgrid_from_waveform(
                        waveform=waveform,
                        args=args,
                    )

                    metric = float(
                        timing_info.get("metric", 0.0)
                    )
                    extracted_symbols = int(
                        timing_info.get(
                            "n_symbols_extracted",
                            0,
                        )
                    )

                    valid = bool(
                        metric >= args.min_pss_metric
                        and extracted_symbols == args.num_symbols
                        and rx_grid_ssb is not None
                        and rx_grid_ssb.shape == (240, 4)
                    )

                except Exception as exception:
                    error = str(exception)
                    valid = False

                loop_time_ms = (
                    1000.0
                    * (time.perf_counter() - loop_start)
                )

                if valid:
                    valid_frames += 1
                else:
                    invalid_frames += 1

                if valid or args.write_invalid:
                    payload = make_payload(
                        iteration=iteration,
                        valid=valid,
                        rx_grid_ssb=rx_grid_ssb,
                        timing_info=timing_info,
                        timing_breakdown=timing_breakdown,
                        center_frequency_hz=actual_frequency,
                        sample_rate_hz=actual_rate,
                        gain_db=actual_gain,
                        channel=args.channel,
                        serial=args.serial,
                        cfo_hz=cfo_hz,
                        cfo_enabled=bool(
                            args.enable_cfo_correction
                            or args.manual_cfo_hz is not None
                        ),
                        capture_time_ms=capture_time_ms,
                        loop_time_ms=loop_time_ms,
                        error=error,
                    )

                    output_file.write(
                        json.dumps(
                            payload,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    output_file.flush()
                    os.fsync(output_file.fileno())

                    frames_written += 1

                if (
                    args.progress_every > 0
                    and iteration % args.progress_every == 0
                ):
                    metric = float(
                        timing_info.get("metric", float("nan"))
                    )

                    print(
                        f"[{iteration:06d}] "
                        f"valid={int(valid)} "
                        f"pss={metric:.3f} "
                        f"written={frames_written} "
                        f"valid_total={valid_frames} "
                        f"invalid_total={invalid_frames} "
                        f"loop={loop_time_ms:.2f} ms "
                        f"err={error}"
                    )

                iteration += 1

        except KeyboardInterrupt:
            print("\nStopping after Ctrl+C.")

    print("\n=== Final statistics ===")
    print(f"iterations:         {iteration}")
    print(f"valid grids:        {valid_frames}")
    print(f"invalid grids:      {invalid_frames}")
    print(f"JSONL lines written:{frames_written}")
    print(f"output:             {output_path}")


if __name__ == "__main__":
    main()
