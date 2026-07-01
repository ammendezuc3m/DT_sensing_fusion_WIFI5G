#!/usr/bin/env python3
"""
Full Python online 5G SSB sensing pipeline.

Pipeline:
    USRP B210
      -> 20 ms IQ capture
      -> optional CFO warmup/correction
      -> PSS/NID2/timing
      -> OFDM demodulation
      -> dataSSB = 360 x 6
      -> rxGridSSB = 240 x 4
      -> lightweight binary inference
      -> local JSON
      -> optional SCP to remote Digital Twin machine

This script replaces the previous MATLAB + external Python online split for
basic end-to-end online operation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from cfo_utils import apply_frequency_correction  # noqa: E402
from capture_online_rxgridssb_dataset_cfo import estimate_cfo_warmup  # noqa: E402
from rxgrid_torch_inference import RxGridTorchBinaryModel  # noqa: E402
from profile_online_datassb_pipeline import (  # noqa: E402
    capture_one_block,
    configure_usrp,
    extract_rxgrid_from_waveform,
    make_rx_streamer,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Full Python online 5G SSB inference + JSON + SCP.")

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
    p.add_argument("--enable-cfo-correction", action="store_true")
    p.add_argument("--manual-cfo-hz", type=float, default=None)
    p.add_argument("--cfo-warmup-iters", type=int, default=30)
    p.add_argument("--cfo-correction-sign", type=float, default=-1.0)
    p.add_argument("--max-cfo-abs-hz", type=float, default=30000.0)

    # Online loop.
    p.add_argument("--num-iters", type=int, default=0, help="0 means run forever.")
    p.add_argument("--warmup-iters", type=int, default=5)
    p.add_argument("--progress-every", type=int, default=1)
    p.add_argument("--json-every", type=int, default=1)
    p.add_argument("--scp-every", type=int, default=1)

    # Inference.
    p.add_argument("--model-config", default="config/generic_5g_binary_model.json")
    p.add_argument("--inference-backend", choices=["threshold", "torch"], default="threshold")
    p.add_argument("--torch-model", default="results/binary_empty_vs_P5_rx/model_rxGridSSB/model.pt")
    p.add_argument("--torch-device", default="cpu")
    p.add_argument("--label-empty", default="EMPTY")
    p.add_argument("--label-person", default="P5")

    # JSON/SCP.
    p.add_argument("--local-json", default="results/online/live_inference_state_5G.json")
    p.add_argument("--log-csv", default="results/online/python_5g_online_inference_log.csv")
    p.add_argument(
        "--remote-target",
        default="nextnet@163.117.140.146:~/AlbertoDir/demo_5G/5G_inference/live_inference_state_5G.json",
        help="SCP target. Empty string disables SCP.",
    )
    p.add_argument("--disable-scp", action="store_true")
    p.add_argument("--scp-timeout-sec", type=float, default=3.0)

    return p.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_scp(local_path: Path, remote_target: str, timeout_sec: float) -> tuple[bool, str]:
    if not remote_target:
        return False, "remote_target_empty"

    try:
        proc = subprocess.run(
            ["scp", "-q", str(local_path), remote_target],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
        )
        if proc.returncode == 0:
            return True, ""
        return False, proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "scp_timeout"
    except Exception as exc:
        return False, str(exc)


def load_threshold_model(path: Path, label_empty: str, label_person: str) -> dict:
    if path.exists():
        model = json.loads(path.read_text(encoding="utf-8"))
    else:
        model = {
            "model_name": "fallback_threshold_model",
            "model_version": "0.0",
            "model_type": "threshold_rx_mean_abs",
            "class_names": [label_empty, label_person],
            "feature": "rxGridSSB_mean_abs",
            "threshold": 30.0,
            "direction": "greater_is_P5",
            "logistic_slope": 0.75,
        }

    model.setdefault("class_names", [label_empty, label_person])
    model.setdefault("threshold", 30.0)
    model.setdefault("direction", "greater_is_P5")
    model.setdefault("logistic_slope", 0.75)

    return model


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def infer_threshold(rx_grid_ssb: np.ndarray, model: dict) -> dict:
    amp = np.abs(rx_grid_ssb)

    feature_value = float(np.mean(amp))
    max_abs = float(np.max(amp))
    median_abs = float(np.median(amp))
    std_abs = float(np.std(amp))

    threshold = float(model.get("threshold", 30.0))
    slope = float(model.get("logistic_slope", 0.75))
    direction = str(model.get("direction", "greater_is_P5"))

    raw = feature_value - threshold
    if direction.lower() in ["lower_is_p5", "less_is_p5"]:
        raw = -raw

    prob_person = sigmoid(slope * raw)
    prob_empty = 1.0 - prob_person

    class_names = model.get("class_names", ["EMPTY", "P5"])
    label_empty = class_names[0]
    label_person = class_names[1]

    if prob_person >= 0.5:
        label = label_person
        class_id = 1
        confidence = prob_person
    else:
        label = label_empty
        class_id = 0
        confidence = prob_empty

    return {
        "label": label,
        "class_id": class_id,
        "confidence": float(confidence),
        "probabilities": {
            label_empty: float(prob_empty),
            label_person: float(prob_person),
        },
        "features": {
            "rxGridSSB_mean_abs": feature_value,
            "rxGridSSB_median_abs": median_abs,
            "rxGridSSB_std_abs": std_abs,
            "rxGridSSB_max_abs": max_abs,
        },
        "model": {
            "model_name": model.get("model_name", "unknown"),
            "model_version": model.get("model_version", "unknown"),
            "model_type": model.get("model_type", "unknown"),
            "threshold": threshold,
            "direction": direction,
            "logistic_slope": slope,
        },
    }


def make_payload(
    iteration: int,
    valid: bool,
    prediction: dict,
    timing_info: dict,
    timing_breakdown: dict,
    loop_time_ms: float,
    capture_time_ms: float,
    cfo_hz: float,
    cfo_enabled: bool,
    rx_grid_ssb: np.ndarray | None,
    error: str = "",
) -> dict:
    label = prediction.get("label", "INVALID") if prediction else "INVALID"
    class_id = prediction.get("class_id", -1) if prediction else -1
    confidence = prediction.get("confidence", 0.0) if prediction else 0.0
    probs = prediction.get("probabilities", {}) if prediction else {}

    person_detected = bool(valid and label not in ["EMPTY", "INVALID", "NONE", "NO_PERSON"])

    payload = {
        "schema_version": "python_5g_ssb_online_v1",
        "source": "python_uspr_b210_ssb_pipeline",
        "timestamp_unix": time.time(),
        "timestamp_utc": now_iso(),
        "iteration": int(iteration),
        "valid": bool(valid),
        "error": error,

        # Compatibility/simple fields for DT consumers.
        "label": label,
        "prediction": label,
        "class_name": label,
        "class_id": int(class_id),
        "confidence": float(confidence),
        "person_detected": person_detected,
        "position": label if person_detected else "none",

        "probabilities": probs,

        "dsp": {
            "cfo_enabled": bool(cfo_enabled),
            "cfo_hz_applied": float(cfo_hz),
            "nid2": int(timing_info.get("nid2", -1)) if timing_info else -1,
            "timing_offset_samples": int(timing_info.get("timing_offset_samples", -1)) if timing_info else -1,
            "timing_offset_ms": float(timing_info.get("timing_offset_ms", -1.0)) if timing_info else -1.0,
            "pss_metric": float(timing_info.get("metric", 0.0)) if timing_info else 0.0,
            "n_symbols_extracted": int(timing_info.get("n_symbols_extracted", 0)) if timing_info else 0,
        },

        "timing_ms": {
            "capture": float(capture_time_ms),
            "pss": float(timing_breakdown.get("pss_time_ms", 0.0)) if timing_breakdown else 0.0,
            "ofdm": float(timing_breakdown.get("ofdm_time_ms", 0.0)) if timing_breakdown else 0.0,
            "dsp_total": float(timing_breakdown.get("total_dsp_time_ms", 0.0)) if timing_breakdown else 0.0,
            "loop_total": float(loop_time_ms),
        },

        "grid": {
            "rxGridSSB_shape": list(rx_grid_ssb.shape) if rx_grid_ssb is not None else None,
        },

        "inference": prediction,
    }

    return payload


def main() -> None:
    args = parse_args()

    local_json = Path(args.local_json)
    log_csv = Path(args.log_csv)
    log_csv.parent.mkdir(parents=True, exist_ok=True)

    model = load_threshold_model(Path(args.model_config), args.label_empty, args.label_person)
    torch_model = None
    if args.inference_backend == "torch":
        torch_model = RxGridTorchBinaryModel(args.torch_model, device=args.torch_device)
        model = {
            "model_name": torch_model.model_name,
            "model_version": "checkpoint",
            "model_type": "torch_cnn2d_abs_phase",
            "class_names": torch_model.classes,
            "threshold": 0.5,
            "direction": "torch_sigmoid",
            "logistic_slope": 1.0,
        }

    print("=== Full Python 5G online inference ===")
    print(f"model config:       {args.model_config}")
    print(f"model type:         {model.get('model_type')}")
    print(f"inference backend:  {args.inference_backend}")
    if args.inference_backend == "torch":
        print(f"torch model:        {args.torch_model}")
    print(f"local JSON:         {local_json}")
    print(f"remote target:      {args.remote_target if not args.disable_scp else 'SCP disabled'}")

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

    print("\n=== Online loop ===")
    print(f"samples/block:      {samples_per_block}")
    print(f"CFO applied:        {cfo_hz:.3f} Hz")
    print(f"CFO sign:           {args.cfo_correction_sign}")
    print(f"num iters:          {'forever' if args.num_iters == 0 else args.num_iters}")

    fieldnames = [
        "iteration",
        "valid",
        "label",
        "confidence",
        "prob_empty",
        "prob_person",
        "rx_mean_abs",
        "pss_metric",
        "cfo_hz",
        "capture_time_ms",
        "pss_time_ms",
        "ofdm_time_ms",
        "dsp_time_ms",
        "loop_time_ms",
        "scp_ok",
        "error",
    ]

    write_header = not log_csv.exists()

    with log_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        i = 0
        while True:
            if args.num_iters > 0 and i >= args.num_iters:
                break

            loop_t0 = time.perf_counter()
            error = ""
            valid = False
            prediction = {}
            timing_info = {}
            timing_breakdown = {}
            rx_grid_ssb = None
            capture_time_ms = float("nan")
            scp_ok = False
            scp_error = ""

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

                _data_ssb, rx_grid_ssb, timing_info, timing_breakdown = extract_rxgrid_from_waveform(
                    waveform=waveform,
                    args=args,
                )

                metric = float(timing_info.get("metric", 0.0))
                n_symbols = int(timing_info.get("n_symbols_extracted", 0))

                valid = bool(
                    metric >= args.min_pss_metric
                    and n_symbols == args.num_symbols
                    and rx_grid_ssb.shape == (240, 4)
                )

                if valid:
                    if args.inference_backend == "torch":
                        prediction = torch_model.predict_proba(rx_grid_ssb)
                    else:
                        prediction = infer_threshold(rx_grid_ssb, model)
                else:
                    prediction = {
                        "label": "INVALID",
                        "class_id": -1,
                        "confidence": 0.0,
                        "probabilities": {},
                        "features": {},
                        "model": {
                            "model_name": model.get("model_name", "unknown"),
                            "model_version": model.get("model_version", "unknown"),
                            "model_type": model.get("model_type", "unknown"),
                        },
                    }

            except Exception as exc:
                error = str(exc)
                valid = False
                prediction = {
                    "label": "INVALID",
                    "class_id": -1,
                    "confidence": 0.0,
                    "probabilities": {},
                    "features": {},
                    "model": {
                        "model_name": model.get("model_name", "unknown"),
                        "model_version": model.get("model_version", "unknown"),
                        "model_type": model.get("model_type", "unknown"),
                    },
                }

            loop_t1 = time.perf_counter()
            loop_time_ms = 1000.0 * (loop_t1 - loop_t0)

            if i % args.json_every == 0:
                payload = make_payload(
                    iteration=i,
                    valid=valid,
                    prediction=prediction,
                    timing_info=timing_info,
                    timing_breakdown=timing_breakdown,
                    loop_time_ms=loop_time_ms,
                    capture_time_ms=capture_time_ms,
                    cfo_hz=cfo_hz,
                    cfo_enabled=bool(args.enable_cfo_correction or args.manual_cfo_hz is not None),
                    rx_grid_ssb=rx_grid_ssb,
                    error=error,
                )

                atomic_write_json(local_json, payload)

                if not args.disable_scp and args.remote_target and i % args.scp_every == 0:
                    scp_ok, scp_error = run_scp(local_json, args.remote_target, args.scp_timeout_sec)

            probs = prediction.get("probabilities", {})
            class_names = model.get("class_names", ["EMPTY", "P5"])
            empty_name = class_names[0]
            person_name = class_names[1]

            row = {
                "iteration": i,
                "valid": int(valid),
                "label": prediction.get("label", "INVALID"),
                "confidence": prediction.get("confidence", 0.0),
                "prob_empty": probs.get(empty_name, np.nan),
                "prob_person": probs.get(person_name, np.nan),
                "rx_mean_abs": prediction.get("features", {}).get("rxGridSSB_mean_abs", np.nan),
                "pss_metric": timing_info.get("metric", np.nan),
                "cfo_hz": cfo_hz,
                "capture_time_ms": capture_time_ms,
                "pss_time_ms": timing_breakdown.get("pss_time_ms", np.nan),
                "ofdm_time_ms": timing_breakdown.get("ofdm_time_ms", np.nan),
                "dsp_time_ms": timing_breakdown.get("total_dsp_time_ms", np.nan),
                "loop_time_ms": loop_time_ms,
                "scp_ok": int(scp_ok),
                "error": error or scp_error,
            }

            writer.writerow(row)
            f.flush()

            if args.progress_every > 0 and (i % args.progress_every == 0):
                print(
                    f"[{i:06d}] "
                    f"valid={int(valid)} "
                    f"label={row['label']} "
                    f"conf={float(row['confidence']):.3f} "
                    f"rx_mean={float(row['rx_mean_abs']) if np.isfinite(row['rx_mean_abs']) else float('nan'):.3f} "
                    f"pss={float(row['pss_metric']) if np.isfinite(row['pss_metric']) else float('nan'):.3f} "
                    f"loop={loop_time_ms:.2f} ms "
                    f"scp={int(scp_ok)} "
                    f"err={error or scp_error}"
                )

            i += 1


if __name__ == "__main__":
    main()
