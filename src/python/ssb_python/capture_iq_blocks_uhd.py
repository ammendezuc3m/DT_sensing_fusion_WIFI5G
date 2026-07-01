#!/usr/bin/env python3
"""
Repeated raw IQ block capture from USRP B210 using UHD.

Goal:
    Capture N blocks of raw IQ, each with the same duration used by the MATLAB
    SSB capture pipeline.

Default:
    Fs = 15.36 Msps
    duration = 20 ms
    samples/block = 307200

Output:
    HDF5 file with:
        waveform[block, sample] complex64
        unix_time_start[block]
        unix_time_end[block]
        t_capture_seconds[block]
        mean_abs[block]
        rms_abs[block]
        max_abs[block]
        cfg_json

Example:
    source .venv_uhd/bin/activate

    python src/python/ssb_python/capture_iq_blocks_uhd.py \
      --serial 34B73C3 \
      --freq 3541.44e6 \
      --rate 15.36e6 \
      --gain 70 \
      --duration-ms 20 \
      --num-blocks 20 \
      --channel 0
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import h5py
import numpy as np
import uhd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Capture repeated raw IQ blocks from USRP B210.")

    p.add_argument("--serial", default="34B73C3")
    p.add_argument("--freq", type=float, default=3541.44e6)
    p.add_argument("--rate", type=float, default=15.36e6)
    p.add_argument("--gain", type=float, default=70.0)
    p.add_argument("--duration-ms", type=float, default=20.0)
    p.add_argument("--num-blocks", type=int, default=20)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="")
    p.add_argument("--settle-sec", type=float, default=0.5)
    p.add_argument("--out-dir", default="data/python_iq_blocks")
    p.add_argument("--prefix", default="iq_blocks_b210")
    p.add_argument("--progress-every", type=int, default=10)

    return p.parse_args()


def metadata_error_to_string(metadata: uhd.types.RXMetadata) -> str:
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

    print(f"Requested rate: {args.rate:.3f} S/s")
    print(f"Actual rate:    {usrp.get_rx_rate(ch):.3f} S/s")
    print(f"Requested freq: {args.freq / 1e6:.6f} MHz")
    print(f"Actual freq:    {usrp.get_rx_freq(ch) / 1e6:.6f} MHz")
    print(f"Requested gain: {args.gain:.2f} dB")
    print(f"Actual gain:    {usrp.get_rx_gain(ch):.2f} dB")

    if args.antenna:
        print(f"Antenna:        {usrp.get_rx_antenna(ch)}")

    return usrp


def capture_one_block(
    rx_streamer,
    total_samples: int,
    max_samps: int,
    timeout: float = 3.0,
) -> np.ndarray:
    metadata = uhd.types.RXMetadata()
    output = np.empty(total_samples, dtype=np.complex64)

    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
    cmd.num_samps = total_samples
    cmd.stream_now = True
    rx_streamer.issue_stream_cmd(cmd)

    num_rx = 0

    while num_rx < total_samples:
        n_this = min(max_samps, total_samples - num_rx)
        buff = np.empty((1, n_this), dtype=np.complex64)

        n_recv = rx_streamer.recv(buff, metadata, timeout=timeout)

        if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
            raise RuntimeError(f"UHD RX error: {metadata_error_to_string(metadata)}")

        if n_recv <= 0:
            raise RuntimeError("UHD returned zero samples.")

        output[num_rx : num_rx + n_recv] = buff[0, :n_recv]
        num_rx += n_recv

    return output


def main() -> None:
    args = parse_args()

    usrp = configure_usrp(args)
    ch = args.channel

    actual_rate = float(usrp.get_rx_rate(ch))
    actual_freq = float(usrp.get_rx_freq(ch))
    actual_gain = float(usrp.get_rx_gain(ch))

    samples_per_block = int(round(actual_rate * args.duration_ms * 1e-3))

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [ch]

    rx_streamer = usrp.get_rx_stream(stream_args)
    max_samps = rx_streamer.get_max_num_samps()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{args.prefix}_{timestamp}.h5"

    cfg = {
        "serial": args.serial,
        "freq_hz_requested": args.freq,
        "rate_hz_requested": args.rate,
        "gain_db_requested": args.gain,
        "duration_ms": args.duration_ms,
        "num_blocks": args.num_blocks,
        "channel": args.channel,
        "antenna": args.antenna,
        "actual_rate_hz": actual_rate,
        "actual_freq_hz": actual_freq,
        "actual_gain_db": actual_gain,
        "samples_per_block": samples_per_block,
        "max_samps_per_recv": int(max_samps),
        "created_unix": time.time(),
    }

    print("\n=== Block capture setup ===")
    print(f"Output file:        {out_path}")
    print(f"Num blocks:         {args.num_blocks}")
    print(f"Duration/block:     {args.duration_ms:.3f} ms")
    print(f"Samples/block:      {samples_per_block}")
    print(f"Max chunk:          {max_samps}")

    with h5py.File(out_path, "w") as f:
        d_waveform = f.create_dataset(
            "waveform",
            shape=(args.num_blocks, samples_per_block),
            dtype=np.complex64,
            chunks=(1, samples_per_block),
            compression="gzip",
            compression_opts=4,
        )

        d_unix_start = f.create_dataset("unix_time_start", shape=(args.num_blocks,), dtype=np.float64)
        d_unix_end = f.create_dataset("unix_time_end", shape=(args.num_blocks,), dtype=np.float64)
        d_t_capture = f.create_dataset("t_capture_seconds", shape=(args.num_blocks,), dtype=np.float64)
        d_mean_abs = f.create_dataset("mean_abs", shape=(args.num_blocks,), dtype=np.float32)
        d_rms_abs = f.create_dataset("rms_abs", shape=(args.num_blocks,), dtype=np.float32)
        d_max_abs = f.create_dataset("max_abs", shape=(args.num_blocks,), dtype=np.float32)
        d_sat_real_pct = f.create_dataset("sat_real_gt_0p99_percent", shape=(args.num_blocks,), dtype=np.float32)
        d_sat_imag_pct = f.create_dataset("sat_imag_gt_0p99_percent", shape=(args.num_blocks,), dtype=np.float32)

        f.attrs["cfg_json"] = json.dumps(cfg, indent=2)

        t_experiment = time.perf_counter()

        for idx in range(args.num_blocks):
            unix_start = time.time()
            t0 = time.perf_counter()

            waveform = capture_one_block(
                rx_streamer=rx_streamer,
                total_samples=samples_per_block,
                max_samps=max_samps,
            )

            t_capture = time.perf_counter() - t0
            unix_end = time.time()

            abs_w = np.abs(waveform)

            d_waveform[idx, :] = waveform
            d_unix_start[idx] = unix_start
            d_unix_end[idx] = unix_end
            d_t_capture[idx] = t_capture
            d_mean_abs[idx] = float(abs_w.mean())
            d_rms_abs[idx] = float(np.sqrt(np.mean(abs_w**2)))
            d_max_abs[idx] = float(abs_w.max())
            d_sat_real_pct[idx] = float(np.mean(np.abs(waveform.real) > 0.99) * 100.0)
            d_sat_imag_pct[idx] = float(np.mean(np.abs(waveform.imag) > 0.99) * 100.0)

            if (idx + 1) % args.progress_every == 0 or idx == 0 or idx + 1 == args.num_blocks:
                elapsed = time.perf_counter() - t_experiment
                rate_blocks = (idx + 1) / elapsed if elapsed > 0 else 0.0
                print(
                    f"[{idx+1:04d}/{args.num_blocks:04d}] "
                    f"t_capture={t_capture*1000:7.2f} ms | "
                    f"mean_abs={d_mean_abs[idx]:.5f} | "
                    f"rms={d_rms_abs[idx]:.5f} | "
                    f"max={d_max_abs[idx]:.5f} | "
                    f"rate={rate_blocks:.2f} blocks/s"
                )

        total_elapsed = time.perf_counter() - t_experiment

        summary = {
            "total_elapsed_seconds": float(total_elapsed),
            "blocks_per_second": float(args.num_blocks / total_elapsed if total_elapsed > 0 else 0.0),
            "mean_capture_seconds": float(np.mean(d_t_capture[:])),
            "median_capture_seconds": float(np.median(d_t_capture[:])),
            "mean_abs_mean": float(np.mean(d_mean_abs[:])),
            "rms_abs_mean": float(np.mean(d_rms_abs[:])),
            "max_abs_max": float(np.max(d_max_abs[:])),
            "sat_real_gt_0p99_percent_mean": float(np.mean(d_sat_real_pct[:])),
            "sat_imag_gt_0p99_percent_mean": float(np.mean(d_sat_imag_pct[:])),
        }

        f.attrs["summary_json"] = json.dumps(summary, indent=2)

    print("\n=== Saved ===")
    print(out_path)

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
