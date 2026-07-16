#!/usr/bin/env python3
"""
Online WiFi beacon CSI receiver.

Architecture:
  - RX thread receives IQ continuously from USRP.
  - Main thread estimates beacon phase and tracks one beacon every 100 ms.
  - CSI is extracted from L-LTF.
  - H5/CSV/metadata and live JSON are written.
  - Optional dummy threshold inference is included for later replacement by a model.
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import uhd
except ImportError:
    uhd = None

try:
    from .wifi_csi import extract_csi_from_packet
    from .wifi_dataset_io import WifiCsiDatasetWriter, WifiCsiSessionMeta, utc_session_id
    from .wifi_legacy_ofdm import SAMPLE_RATE
    from .wifi_live_json import atomic_write_json, utc_now_iso
    from .wifi_ltf_tracker import (
        estimate_phase_from_offsets,
        refine_packet_start_ltf,
        scan_ltf_seeds,
    )
except ImportError:
    from wifi_csi import extract_csi_from_packet
    from wifi_dataset_io import WifiCsiDatasetWriter, WifiCsiSessionMeta, utc_session_id
    from wifi_legacy_ofdm import SAMPLE_RATE
    from wifi_live_json import atomic_write_json, utc_now_iso
    from wifi_ltf_tracker import (
        estimate_phase_from_offsets,
        refine_packet_start_ltf,
        scan_ltf_seeds,
    )


@dataclass
class RxBlock:
    samples: np.ndarray
    global_start: int
    timestamp_unix: float
    timestamp_usrp: float


@dataclass
class RxStats:
    samples_received: int = 0
    blocks_received: int = 0
    overflow_count: int = 0
    dropped_blocks: int = 0
    last_error: str = ""


def build_usrp_args(serial: str) -> str:
    return f"serial={serial}" if serial else ""


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def run_dummy_inference(
    csi: np.ndarray,
    backend: str,
    threshold: float,
    slope: float,
    label_low: str,
    label_high: str,
) -> dict[str, Any]:
    amp_mean = float(np.mean(np.abs(csi)))
    amp_std = float(np.std(np.abs(csi)))
    phase_std = float(np.std(np.angle(csi)))

    if backend == "none":
        return {
            "backend": "none",
            "label": "UNTRAINED",
            "class_id": -1,
            "confidence": 0.0,
            "features": {
                "csi_amp_mean": amp_mean,
                "csi_amp_std": amp_std,
                "csi_phase_std": phase_std,
            },
            "probabilities": {},
        }

    score = slope * (amp_mean - threshold)
    prob_high = sigmoid(score)
    prob_low = 1.0 - prob_high

    if prob_high >= 0.5:
        label = label_high
        class_id = 1
        confidence = prob_high
    else:
        label = label_low
        class_id = 0
        confidence = prob_low

    return {
        "backend": "threshold",
        "label": label,
        "class_id": class_id,
        "confidence": float(confidence),
        "features": {
            "csi_amp_mean": amp_mean,
            "csi_amp_std": amp_std,
            "csi_phase_std": phase_std,
            "threshold": float(threshold),
            "slope": float(slope),
        },
        "probabilities": {
            label_low: float(prob_low),
            label_high: float(prob_high),
        },
    }


def rx_thread_main(
    args: argparse.Namespace,
    out_queue: queue.Queue,
    stop_event: threading.Event,
    ready_event: threading.Event,
    stats: RxStats,
) -> None:
    if uhd is None:
        stats.last_error = "Python package 'uhd' not available"
        ready_event.set()
        return

    try:
        usrp = uhd.usrp.MultiUSRP(build_usrp_args(args.serial))
        usrp.set_rx_rate(args.rate, args.channel)
        usrp.set_rx_freq(uhd.types.TuneRequest(args.freq), args.channel)
        usrp.set_rx_gain(args.gain, args.channel)

        if args.antenna:
            usrp.set_rx_antenna(args.antenna, args.channel)

        usrp.set_time_now(uhd.types.TimeSpec(0.0))
        time.sleep(0.1)

        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [args.channel]
        rx_streamer = usrp.get_rx_stream(stream_args)

        block_samps = int(round(args.block_ms * 1e-3 * args.rate))
        block_samps = max(block_samps, 4096)

        buff = np.zeros(block_samps, dtype=np.complex64)
        md = uhd.types.RXMetadata()

        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
        cmd.stream_now = True
        rx_streamer.issue_stream_cmd(cmd)

        ready_event.set()

        sample_counter = 0

        while not stop_event.is_set():
            n = rx_streamer.recv(buff, md, timeout=1.0)

            if md.error_code != uhd.types.RXMetadataErrorCode.none:
                stats.overflow_count += 1
                stats.last_error = md.strerror()
                continue

            if n <= 0:
                continue

            try:
                ts_usrp = md.time_spec.get_real_secs()
            except Exception:
                ts_usrp = float("nan")

            block = RxBlock(
                samples=buff[:n].copy(),
                global_start=sample_counter,
                timestamp_unix=time.time(),
                timestamp_usrp=ts_usrp,
            )

            sample_counter += n
            stats.samples_received = sample_counter
            stats.blocks_received += 1

            try:
                out_queue.put_nowait(block)
            except queue.Full:
                try:
                    _ = out_queue.get_nowait()
                except queue.Empty:
                    pass
                stats.dropped_blocks += 1
                try:
                    out_queue.put_nowait(block)
                except queue.Full:
                    stats.dropped_blocks += 1

        stop_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        rx_streamer.issue_stream_cmd(stop_cmd)

    except Exception as exc:
        stats.last_error = repr(exc)
        ready_event.set()
        stop_event.set()


def write_state(path: str, payload: dict[str, Any]) -> None:
    if path:
        atomic_write_json(path, payload)


def main() -> None:
    p = argparse.ArgumentParser()

    p.add_argument("--serial", required=True)
    p.add_argument("--freq", type=float, default=2.412e9)
    p.add_argument("--rate", type=float, default=SAMPLE_RATE)
    p.add_argument("--gain", type=float, default=35.0)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="RX2")

    p.add_argument("--duration-sec", type=float, default=60.0)
    p.add_argument("--block-ms", type=float, default=200.0)
    p.add_argument("--queue-blocks", type=int, default=8)
    p.add_argument("--max-drain-blocks", type=int, default=4)

    p.add_argument("--init-seconds", type=float, default=1.0)
    p.add_argument("--tx-period-ms", type=float, default=100.0)
    p.add_argument("--seed-threshold", type=float, default=0.10)
    p.add_argument("--accept-threshold", type=float, default=0.10)
    p.add_argument("--search-radius-ms", type=float, default=5.0)
    p.add_argument("--phase-bin-ms", type=float, default=2.0)
    p.add_argument("--buffer-keep-sec", type=float, default=0.5)

    p.add_argument("--label", default="empty")
    p.add_argument("--scene", default="static")
    p.add_argument("--ssid-filter", default="SENSING_WIFI")
    p.add_argument("--bssid-filter", default="02:11:22:33:44:55")
    p.add_argument("--wifi-channel", type=int, default=1)
    p.add_argument("--beacon-interval-tu", type=int, default=98)
    p.add_argument("--output-root", default="data/wifi_csi_datasets")
    p.add_argument("--notes", default="")

    p.add_argument("--local-json", default="results/wifi_online/live_wifi_rx_state.json")
    p.add_argument("--flush-every-packets", type=int, default=100)
    p.add_argument("--progress-every", type=int, default=50)
    p.add_argument("--heartbeat-sec", type=float, default=2.0)

    p.add_argument("--inference-backend", choices=["none", "threshold"], default="none")
    p.add_argument("--model-path", default="", help="Optional future WiFi model path. Currently stored in JSON/metadata; torch backend will use it later.")
    p.add_argument("--inference-threshold", type=float, default=0.20)
    p.add_argument("--inference-slope", type=float, default=25.0)
    p.add_argument("--label-low", default="EMPTY")
    p.add_argument("--label-high", default="PERSON")

    args = p.parse_args()

    if abs(args.rate - SAMPLE_RATE) > 1:
        raise SystemExit("This implementation expects --rate 20e6.")

    period_samples = int(round(args.tx_period_ms * 1e-3 * args.rate))
    search_radius = int(round(args.search_radius_ms * 1e-3 * args.rate))
    phase_bin = int(round(args.phase_bin_ms * 1e-3 * args.rate))
    min_separation = int(round(0.060 * args.rate))
    keep_samples = int(round(args.buffer_keep_sec * args.rate))

    session_id = utc_session_id(args.label, args.scene)

    meta = WifiCsiSessionMeta(
        session_id=session_id,
        label=args.label,
        scene=args.scene,
        ssid_filter=args.ssid_filter,
        bssid_filter=args.bssid_filter,
        freq_hz=args.freq,
        sample_rate_hz=args.rate,
        gain_db=args.gain,
        channel=args.wifi_channel,
        beacon_interval_tu=args.beacon_interval_tu,
        tx_period_ms=args.tx_period_ms,
        notes=args.notes,
    )

    writer = WifiCsiDatasetWriter(args.output_root, meta)

    q: queue.Queue[RxBlock] = queue.Queue(maxsize=args.queue_blocks)
    stop_event = threading.Event()
    ready_event = threading.Event()
    stats = RxStats()

    rx_thread = threading.Thread(
        target=rx_thread_main,
        args=(args, q, stop_event, ready_event, stats),
        daemon=True,
    )

    print("WiFi online beacon CSI RX")
    print(f"  serial: {args.serial}")
    print(f"  antenna: {args.antenna}")
    print(f"  freq: {args.freq}")
    print(f"  rate: {args.rate}")
    print(f"  gain: {args.gain}")
    print(f"  tx period: {args.tx_period_ms} ms")
    print(f"  period samples: {period_samples}")
    print(f"  init seconds: {args.init_seconds}")
    print(f"  seed threshold: {args.seed_threshold}")
    print(f"  accept threshold: {args.accept_threshold}")
    print(f"  output: {writer.session_dir}")
    print(f"  JSON: {args.local_json}")

    base_state = {
        "schema_version": "wifi_beacon_csi_online_rx_v1",
        "role": "rx",
        "status": "initializing",
        "valid": True,
        "error": None,
        "session_id": session_id,
        "output_dir": str(writer.session_dir),
        "h5_path": str(writer.h5_path),
        "csv_path": str(writer.csv_path),
        "metadata_path": str(writer.meta_path),
        "freq_hz": args.freq,
        "sample_rate_hz": args.rate,
        "gain_db": args.gain,
        "antenna": args.antenna,
        "tx_period_ms": args.tx_period_ms,
        "beacon_interval_tu": args.beacon_interval_tu,
        "ssid_filter": args.ssid_filter,
        "bssid_filter": args.bssid_filter,
        "model_path": args.model_path,
    }

    write_state(args.local_json, {
        **base_state,
        "timestamp_utc": utc_now_iso(),
        "packets_detected": 0,
    })

    rx_thread.start()
    ready_event.wait(timeout=10.0)

    if stats.last_error:
        raise SystemExit(f"RX thread failed during init: {stats.last_error}")

    t_start = time.time()
    t_end = t_start + args.duration_sec

    # Initial buffer for phase estimation.
    init_blocks: list[RxBlock] = []
    init_samples = 0
    init_target = int(round(args.init_seconds * args.rate))

    print("Collecting initial samples for phase estimation...")

    while init_samples < init_target:
        if time.time() > t_end:
            break

        try:
            b = q.get(timeout=2.0)
        except queue.Empty:
            continue

        init_blocks.append(b)
        init_samples += len(b.samples)

    if not init_blocks:
        stop_event.set()
        raise SystemExit("No RX blocks received.")

    buffer = np.concatenate([b.samples for b in init_blocks]).astype(np.complex64)
    buffer_global_start = init_blocks[0].global_start
    first_unix = init_blocks[0].timestamp_unix
    first_usrp = init_blocks[0].timestamp_usrp

    seeds_local = scan_ltf_seeds(
        iq=buffer,
        rate=args.rate,
        seed_seconds=args.init_seconds,
        threshold=args.seed_threshold,
        min_separation_samples=min_separation,
    )

    if not seeds_local:
        stop_event.set()
        writer.close()
        write_state(args.local_json, {
            **base_state,
            "timestamp_utc": utc_now_iso(),
            "status": "no_seed",
            "valid": False,
            "error": "No L-LTF seed detections found",
            "rx_stats": stats.__dict__,
        })
        raise SystemExit("No L-LTF seed detections found. Try lower threshold, more gain, or check TX.")

    seed_global_offsets = [buffer_global_start + off for off, _m in seeds_local]
    phase = estimate_phase_from_offsets(seed_global_offsets, period_samples, phase_bin)

    print(f"Seed detections: {len(seed_global_offsets)}")
    print(f"Estimated phase: {phase} samples = {phase / args.rate:.9f} s")

    # Next expected beacon after the current buffer start.
    buffer_global_end = buffer_global_start + len(buffer)
    k = int(math.floor((buffer_global_start - phase) / period_samples)) - 1

    while phase + k * period_samples < buffer_global_start:
        k += 1

    next_expected = phase + k * period_samples

    packet_count = 0
    missed_count = 0
    last_flush_count = 0
    last_heartbeat = 0.0
    last_packet_state = None
    final_error = None

    print("Online tracking started.")

    try:
        while time.time() < t_end:
            # Pull only a bounded number of blocks.
            # Do NOT drain the queue forever; otherwise this loop can starve
            # the actual beacon processing when RX is continuously producing data.
            drained = 0

            while drained < args.max_drain_blocks:
                try:
                    b = q.get(timeout=0.02 if drained == 0 else 0.0)
                except queue.Empty:
                    break

                drained += 1

                if len(buffer) == 0:
                    buffer = b.samples.copy()
                    buffer_global_start = b.global_start
                else:
                    buffer_global_end = buffer_global_start + len(buffer)

                    # If blocks are not contiguous because the queue dropped data,
                    # reset the rolling buffer and advance expected beacon index.
                    if b.global_start > buffer_global_end:
                        while next_expected < b.global_start:
                            missed_count += 1
                            next_expected += period_samples

                        buffer = b.samples.copy()
                        buffer_global_start = b.global_start
                    else:
                        overlap = buffer_global_end - b.global_start

                        if overlap < len(b.samples):
                            append = b.samples[max(0, overlap):]

                            if len(append) > 0:
                                buffer = np.concatenate([buffer, append]).astype(np.complex64)

                buffer_global_end = buffer_global_start + len(buffer)

            if len(buffer) == 0:
                continue

            buffer_global_end = buffer_global_start + len(buffer)

            # Process every expected beacon whose full search window is already inside the buffer.
            while next_expected + search_radius + 6000 < buffer_global_end:
                if next_expected - search_radius < buffer_global_start:
                    missed_count += 1
                    next_expected += period_samples
                    continue

                local_expected = int(next_expected - buffer_global_start)

                off_local, metric = refine_packet_start_ltf(
                    buffer,
                    expected_start=local_expected,
                    search_radius_samples=search_radius,
                )

                if metric < args.accept_threshold:
                    missed_count += 1
                    next_expected += period_samples
                    continue

                if off_local + 2400 > len(buffer):
                    break

                pkt = buffer[off_local:off_local + 2400].astype(np.complex64)

                try:
                    csi, cfo_hz = extract_csi_from_packet(pkt, sample_rate=args.rate)
                except Exception:
                    missed_count += 1
                    next_expected += period_samples
                    continue

                global_offset = buffer_global_start + off_local
                rel_from_first = global_offset - buffer_global_start

                # Better approximate timestamps using the first block.
                timestamp_unix = first_unix + (global_offset - init_blocks[0].global_start) / args.rate
                if np.isfinite(first_usrp):
                    timestamp_usrp = first_usrp + (global_offset - init_blocks[0].global_start) / args.rate
                else:
                    timestamp_usrp = float("nan")

                rx_power = float(10 * np.log10(np.mean(np.abs(pkt[:320]) ** 2) + 1e-12))

                inference = run_dummy_inference(
                    csi=csi,
                    backend=args.inference_backend,
                    threshold=args.inference_threshold,
                    slope=args.inference_slope,
                    label_low=args.label_low,
                    label_high=args.label_high,
                )

                writer.append(
                    csi=csi,
                    timestamp_unix=timestamp_unix,
                    timestamp_usrp_rx=timestamp_usrp,
                    packet_index=packet_count,
                    timing_offset_samples=int(global_offset),
                    cfo_hz=float(cfo_hz),
                    ltf_metric=float(metric),
                    rx_power_db=float(rx_power),
                )

                last_packet_state = {
                    "packet_index": packet_count,
                    "timestamp_unix": timestamp_unix,
                    "timestamp_usrp_rx": timestamp_usrp,
                    "global_offset_samples": int(global_offset),
                    "expected_offset_samples": int(next_expected),
                    "timing_error_samples": int(global_offset - next_expected),
                    "ltf_metric": float(metric),
                    "cfo_hz": float(cfo_hz),
                    "rx_power_db": float(rx_power),
                    "csi_shape": list(csi.shape),
                    "inference": inference,
                }

                packet_count += 1

                if args.progress_every > 0 and packet_count % args.progress_every == 0:
                    print(
                        f"packets={packet_count} "
                        f"missed={missed_count} "
                        f"metric={metric:.3f} "
                        f"cfo={cfo_hz:.1f}Hz "
                        f"power={rx_power:.1f}dB "
                        f"label={inference['label']} "
                        f"conf={inference['confidence']:.3f}"
                    )

                write_state(args.local_json, {
                    **base_state,
                    "timestamp_utc": utc_now_iso(),
                    "status": "receiving",
                    "packets_detected": packet_count,
                    "missed_beacons": missed_count,
                    "phase_samples": int(phase),
                    "period_samples": int(period_samples),
                    "last_packet": last_packet_state,
                    "rx_stats": stats.__dict__,
                    "queue_size": q.qsize(),
                    "elapsed_sec": time.time() - t_start,
                })

                if (
                    args.flush_every_packets > 0
                    and packet_count > 0
                    and packet_count - last_flush_count >= args.flush_every_packets
                ):
                    writer.flush()
                    last_flush_count = packet_count

                next_expected += period_samples

            # Prune old buffer.
            min_needed = next_expected - search_radius - 8000
            max_keep_start = buffer_global_end - keep_samples
            new_start = max(buffer_global_start, min(min_needed, max_keep_start))

            if new_start > buffer_global_start:
                drop = int(new_start - buffer_global_start)
                if drop > 0 and drop < len(buffer):
                    buffer = buffer[drop:].copy()
                    buffer_global_start += drop

            now = time.time()
            if now - last_heartbeat >= args.heartbeat_sec:
                write_state(args.local_json, {
                    **base_state,
                    "timestamp_utc": utc_now_iso(),
                    "status": "receiving",
                    "packets_detected": packet_count,
                    "missed_beacons": missed_count,
                    "phase_samples": int(phase),
                    "period_samples": int(period_samples),
                    "last_packet": last_packet_state,
                    "rx_stats": stats.__dict__,
                    "queue_size": q.qsize(),
                    "elapsed_sec": now - t_start,
                })
                last_heartbeat = now

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        final_error = "KeyboardInterrupt"

    except Exception as exc:
        final_error = repr(exc)
        write_state(args.local_json, {
            **base_state,
            "timestamp_utc": utc_now_iso(),
            "status": "error",
            "valid": False,
            "error": final_error,
            "packets_detected": packet_count,
            "missed_beacons": missed_count,
            "last_packet": last_packet_state,
            "rx_stats": stats.__dict__,
        })
        raise

    finally:
        stop_event.set()
        rx_thread.join(timeout=2.0)

        writer.close()

        write_state(args.local_json, {
            **base_state,
            "timestamp_utc": utc_now_iso(),
            "status": "stopped",
            "valid": final_error is None or final_error == "KeyboardInterrupt",
            "error": final_error,
            "packets_detected": packet_count,
            "missed_beacons": missed_count,
            "last_packet": last_packet_state,
            "rx_stats": stats.__dict__,
            "elapsed_sec": time.time() - t_start,
        })

        print("RX stopped.")
        print(f"Detected CSI packets: {packet_count}")
        print(f"Missed beacons: {missed_count}")
        print(f"H5: {writer.h5_path}")
        print(f"CSV: {writer.csv_path}")
        print(f"Metadata: {writer.meta_path}")
        print(f"JSON: {args.local_json}")
        print(f"RX stats: {stats}")


if __name__ == "__main__":
    main()
