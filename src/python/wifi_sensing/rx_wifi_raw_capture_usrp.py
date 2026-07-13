#!/usr/bin/env python3
"""
Minimal raw IQ capture from USRP.

Purpose:
  - Receive at 20 Msps with almost no processing.
  - Avoid online CSI overhead.
  - Save raw IQ to .npy for offline WiFi beacon CSI processing.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

try:
    import uhd
except ImportError:
    uhd = None


def build_usrp_args(serial: str) -> str:
    return f"serial={serial}" if serial else ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--serial", required=True)
    p.add_argument("--freq", type=float, default=2.412e9)
    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--gain", type=float, default=30.0)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="")
    p.add_argument("--duration-sec", type=float, default=3.0)
    p.add_argument("--block-ms", type=float, default=50.0)
    p.add_argument("--output-npy", default="results/wifi_debug/raw_wifi_capture.npy")
    args = p.parse_args()

    if uhd is None:
        raise SystemExit("Python package 'uhd' not available.")

    total_samps = int(round(args.duration_sec * args.rate))
    block_samps = int(round(args.block_ms * 1e-3 * args.rate))

    print("Raw WiFi IQ capture")
    print(f"  serial: {args.serial}")
    print(f"  freq: {args.freq}")
    print(f"  rate: {args.rate}")
    print(f"  gain: {args.gain}")
    print(f"  duration: {args.duration_sec} s")
    print(f"  total samples: {total_samps}")
    print(f"  estimated RAM: {total_samps * 8 / 1e6:.1f} MB")
    print(f"  output: {args.output_npy}")

    out = Path(args.output_npy)
    out.parent.mkdir(parents=True, exist_ok=True)

    iq = np.empty(total_samps, dtype=np.complex64)

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

    buff = np.zeros(block_samps, dtype=np.complex64)
    md = uhd.types.RXMetadata()

    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    cmd.stream_now = True
    rx_streamer.issue_stream_cmd(cmd)

    write_pos = 0
    overflow_count = 0
    t0 = time.time()

    try:
        while write_pos < total_samps:
            remaining = total_samps - write_pos
            n_req = min(block_samps, remaining)

            n = rx_streamer.recv(buff[:n_req], md, timeout=2.0)

            if md.error_code != uhd.types.RXMetadataErrorCode.none:
                overflow_count += 1
                continue

            if n <= 0:
                continue

            iq[write_pos:write_pos + n] = buff[:n]
            write_pos += n

            if write_pos % int(args.rate) < n:
                print(f"captured {write_pos / args.rate:.2f} s / {args.duration_sec:.2f} s")

    finally:
        stop_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        rx_streamer.issue_stream_cmd(stop_cmd)

    elapsed = time.time() - t0

    iq = iq[:write_pos]
    np.save(out, iq)

    print("Done")
    print(f"  captured samples: {write_pos}")
    print(f"  captured seconds: {write_pos / args.rate:.3f}")
    print(f"  elapsed wall time: {elapsed:.3f}")
    print(f"  overflow_count: {overflow_count}")
    print(f"  saved: {out}")


if __name__ == "__main__":
    main()
