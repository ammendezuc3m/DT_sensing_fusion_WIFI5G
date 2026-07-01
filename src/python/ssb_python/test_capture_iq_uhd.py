#!/usr/bin/env python3
"""
Minimal UHD IQ capture test for USRP B210.

Goal:
    Capture a short raw IQ waveform from the B210 and save it to .npz.

This is the first step towards replacing the MATLAB SSB capture pipeline.

Example:
    source .venv_uhd/bin/activate

    python src/python/ssb_python/test_capture_iq_uhd.py \
      --serial 34B73C3 \
      --freq 3541.44e6 \
      --rate 15.36e6 \
      --gain 70 \
      --duration-ms 20 \
      --channel 0 \
      --antenna RX2
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import uhd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture raw IQ samples from USRP B210 using UHD.")

    parser.add_argument("--serial", default="34B73C3", help="USRP serial number.")
    parser.add_argument("--freq", type=float, default=3541.44e6, help="RX center frequency in Hz.")
    parser.add_argument("--rate", type=float, default=15.36e6, help="RX sample rate in samples/s.")
    parser.add_argument("--gain", type=float, default=70.0, help="RX gain in dB.")
    parser.add_argument("--duration-ms", type=float, default=20.0, help="Capture duration in ms.")
    parser.add_argument("--channel", type=int, default=0, help="RX channel index.")
    parser.add_argument("--antenna", default="", help="RX antenna name. Example: RX2, TX/RX. Empty keeps UHD default.")
    parser.add_argument("--settle-sec", type=float, default=0.5, help="Settling time after configuring USRP.")
    parser.add_argument("--out-dir", default="data/python_iq_captures", help="Output directory.")
    parser.add_argument("--prefix", default="iq_b210", help="Output filename prefix.")

    return parser.parse_args()


def rx_metadata_error_to_string(metadata: uhd.types.RXMetadata) -> str:
    try:
        return metadata.strerror()
    except Exception:
        return str(metadata.error_code)


def configure_usrp(args: argparse.Namespace) -> uhd.usrp.MultiUSRP:
    usrp = uhd.usrp.MultiUSRP(f"serial={args.serial}")

    ch = args.channel

    print("=== USRP info ===")
    print(f"Motherboard: {usrp.get_mboard_name()}")
    print(f"RX channels: {usrp.get_rx_num_channels()}")

    print("\n=== Configuring RX ===")
    usrp.set_rx_rate(args.rate, ch)
    usrp.set_rx_freq(uhd.types.TuneRequest(args.freq), ch)
    usrp.set_rx_gain(args.gain, ch)

    if args.antenna:
        usrp.set_rx_antenna(args.antenna, ch)

    time.sleep(args.settle_sec)

    actual_rate = usrp.get_rx_rate(ch)
    actual_freq = usrp.get_rx_freq(ch)
    actual_gain = usrp.get_rx_gain(ch)

    print(f"Requested rate: {args.rate:.3f} S/s")
    print(f"Actual rate:    {actual_rate:.3f} S/s")
    print(f"Requested freq: {args.freq / 1e6:.6f} MHz")
    print(f"Actual freq:    {actual_freq / 1e6:.6f} MHz")
    print(f"Requested gain: {args.gain:.2f} dB")
    print(f"Actual gain:    {actual_gain:.2f} dB")

    if args.antenna:
        try:
            print(f"Antenna:        {usrp.get_rx_antenna(ch)}")
        except Exception:
            pass

    return usrp


def capture_iq(
    usrp: uhd.usrp.MultiUSRP,
    channel: int,
    rate: float,
    duration_ms: float,
) -> tuple[np.ndarray, dict]:
    total_samples = int(round(rate * duration_ms * 1e-3))

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [channel]

    rx_streamer = usrp.get_rx_stream(stream_args)
    max_samps = rx_streamer.get_max_num_samps()

    metadata = uhd.types.RXMetadata()

    output = np.empty(total_samples, dtype=np.complex64)

    print("\n=== Capture ===")
    print(f"Duration:       {duration_ms:.3f} ms")
    print(f"Total samples:  {total_samples}")
    print(f"Max chunk:      {max_samps}")

    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
    cmd.num_samps = total_samples
    cmd.stream_now = True

    rx_streamer.issue_stream_cmd(cmd)

    num_rx = 0
    t0 = time.perf_counter()

    while num_rx < total_samples:
        n_this = min(max_samps, total_samples - num_rx)
        buff = np.empty((1, n_this), dtype=np.complex64)

        n_recv = rx_streamer.recv(buff, metadata, timeout=3.0)

        if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
            raise RuntimeError(f"UHD RX error: {rx_metadata_error_to_string(metadata)}")

        if n_recv <= 0:
            raise RuntimeError("UHD returned zero samples.")

        output[num_rx : num_rx + n_recv] = buff[0, :n_recv]
        num_rx += n_recv

    elapsed = time.perf_counter() - t0

    stats = {
        "requested_samples": total_samples,
        "received_samples": int(num_rx),
        "elapsed_seconds": float(elapsed),
        "capture_rate_samples_per_second": float(num_rx / elapsed if elapsed > 0 else 0.0),
        "mean_abs": float(np.mean(np.abs(output))),
        "max_abs": float(np.max(np.abs(output))),
        "rms_abs": float(np.sqrt(np.mean(np.abs(output) ** 2))),
    }

    return output, stats


def save_capture(
    waveform: np.ndarray,
    args: argparse.Namespace,
    stats: dict,
    actual_rate: float,
    actual_freq: float,
    actual_gain: float,
) -> Path:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{args.prefix}_{timestamp}.npz"

    cfg = {
        "serial": args.serial,
        "freq_hz_requested": args.freq,
        "rate_hz_requested": args.rate,
        "gain_db_requested": args.gain,
        "duration_ms": args.duration_ms,
        "channel": args.channel,
        "antenna": args.antenna,
        "actual_rate_hz": actual_rate,
        "actual_freq_hz": actual_freq,
        "actual_gain_db": actual_gain,
        "created_unix": time.time(),
        "stats": stats,
    }

    np.savez_compressed(
        out_path,
        waveform=waveform.astype(np.complex64),
        cfg_json=json.dumps(cfg, indent=2),
    )

    return out_path


def main() -> None:
    args = parse_args()

    usrp = configure_usrp(args)

    actual_rate = usrp.get_rx_rate(args.channel)
    actual_freq = usrp.get_rx_freq(args.channel)
    actual_gain = usrp.get_rx_gain(args.channel)

    waveform, stats = capture_iq(
        usrp=usrp,
        channel=args.channel,
        rate=actual_rate,
        duration_ms=args.duration_ms,
    )

    out_path = save_capture(
        waveform=waveform,
        args=args,
        stats=stats,
        actual_rate=actual_rate,
        actual_freq=actual_freq,
        actual_gain=actual_gain,
    )

    print("\n=== Saved ===")
    print(out_path)

    print("\n=== Stats ===")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
