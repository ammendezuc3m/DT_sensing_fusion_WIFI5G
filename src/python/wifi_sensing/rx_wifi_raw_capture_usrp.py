#!/usr/bin/env python3
"""
Raw IQ capture for offline WiFi receiver development.

The capture is saved as NPZ with IQ and radio metadata. No packet detection or
CSI processing runs in the receive loop.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    import uhd
except ImportError:
    uhd = None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--serial", required=True)
    p.add_argument("--freq", type=float, default=2.412e9)
    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--gain", type=float, default=20.0)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="RX2")
    p.add_argument("--bandwidth", type=float, default=20e6)
    p.add_argument("--duration-sec", type=float, default=5.0)
    p.add_argument("--block-samples", type=int, default=262144)
    p.add_argument("--output", default="results/wifi_rx/raw_wifi_capture.npz")
    args = p.parse_args()

    if uhd is None:
        raise SystemExit("Python package 'uhd' is not available")
    if args.duration_sec <= 0:
        raise SystemExit("--duration-sec must be positive")
    if args.block_samples < 4096:
        raise SystemExit("--block-samples must be at least 4096")

    total_samples = int(round(args.duration_sec * args.rate))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    usrp = uhd.usrp.MultiUSRP(f"serial={args.serial}")
    usrp.set_rx_rate(args.rate, args.channel)
    usrp.set_rx_freq(uhd.types.TuneRequest(args.freq), args.channel)
    usrp.set_rx_gain(args.gain, args.channel)
    if hasattr(usrp, "set_rx_bandwidth"):
        usrp.set_rx_bandwidth(args.bandwidth, args.channel)
    if args.antenna:
        usrp.set_rx_antenna(args.antenna, args.channel)

    actual_rate = float(usrp.get_rx_rate(args.channel))
    actual_freq = float(usrp.get_rx_freq(args.channel))
    actual_gain = float(usrp.get_rx_gain(args.channel))
    actual_antenna = str(usrp.get_rx_antenna(args.channel))

    print("Raw WiFi IQ capture")
    print(f"  serial: {args.serial}")
    print(f"  actual rate: {actual_rate / 1e6:.6f} Msps")
    print(f"  actual frequency: {actual_freq / 1e6:.6f} MHz")
    print(f"  actual gain: {actual_gain:.2f} dB")
    print(f"  actual antenna: {actual_antenna}")
    print(f"  duration: {args.duration_sec:.3f} s")
    print(f"  total samples: {total_samples}")
    print(f"  RAM for IQ: {total_samples * 8 / 1e6:.1f} MB")
    print(f"  output: {output}")

    iq = np.empty(total_samples, dtype=np.complex64)
    buff = np.empty(args.block_samples, dtype=np.complex64)

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [args.channel]
    streamer = usrp.get_rx_stream(stream_args)
    md = uhd.types.RXMetadata()

    usrp.set_time_now(uhd.types.TimeSpec(0.0))
    time.sleep(0.1)

    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    cmd.stream_now = True
    streamer.issue_stream_cmd(cmd)

    written = 0
    overflow_count = 0
    timeout_count = 0
    metadata_errors: dict[str, int] = {}
    first_usrp_time = None
    started_wall = time.time()

    try:
        while written < total_samples:
            request = min(args.block_samples, total_samples - written)
            n = streamer.recv(buff[:request], md, timeout=2.0)

            if md.error_code != uhd.types.RXMetadataErrorCode.none:
                name = str(md.error_code)
                metadata_errors[name] = metadata_errors.get(name, 0) + 1
                if md.error_code == uhd.types.RXMetadataErrorCode.overflow:
                    overflow_count += 1
                elif md.error_code == uhd.types.RXMetadataErrorCode.timeout:
                    timeout_count += 1
                continue

            if n <= 0:
                continue

            if first_usrp_time is None and getattr(md, "has_time_spec", False):
                first_usrp_time = float(md.time_spec.get_real_secs())

            iq[written:written + n] = buff[:n]
            written += n

            if written % int(actual_rate) < n:
                print(f"  captured {written / actual_rate:.2f} / {args.duration_sec:.2f} s")

    finally:
        streamer.issue_stream_cmd(
            uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        )

    elapsed = time.time() - started_wall
    iq = iq[:written]

    metadata = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "serial": args.serial,
        "freq_hz": actual_freq,
        "sample_rate_hz": actual_rate,
        "gain_db": actual_gain,
        "antenna": actual_antenna,
        "bandwidth_hz": args.bandwidth,
        "requested_duration_sec": args.duration_sec,
        "captured_samples": written,
        "captured_duration_sec": written / actual_rate,
        "elapsed_wall_sec": elapsed,
        "overflow_count": overflow_count,
        "timeout_count": timeout_count,
        "metadata_errors": metadata_errors,
        "first_usrp_time": first_usrp_time,
    }

    np.savez_compressed(
        output,
        iq=iq,
        metadata_json=np.asarray([json.dumps(metadata)]),
    )

    print("Capture finished")
    print(f"  captured samples: {written}")
    print(f"  captured duration: {written / actual_rate:.6f} s")
    print(f"  overflow count: {overflow_count}")
    print(f"  timeout count: {timeout_count}")
    print(f"  metadata errors: {metadata_errors or 'none'}")
    print(f"  saved: {output}")


if __name__ == "__main__":
    main()
