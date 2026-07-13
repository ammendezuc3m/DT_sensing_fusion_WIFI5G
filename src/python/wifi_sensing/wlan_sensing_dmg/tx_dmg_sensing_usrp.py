#!/usr/bin/env python3
"""
USRP transmitter for a DMG/802.11bf-inspired sensing PPDU.

This transmitter repeatedly sends a scaled DMG-like single-carrier waveform:

    guard
    STF Golay repetitions
    CEF Golay complementary pair
    known header-like field
    known sensing payload
    TRN-like training field
    guard

Default test profile:
    center frequency: 2.412 GHz
    sample rate:      20 Msps
    period:           100 ms
    antenna:          TX/RX
    gain:             20 dB

This is waveform-only. It does not implement full IEEE 802.11bf MAC
negotiation, association, feedback reporting, or commercial interoperability.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from waveform import (
    DmgSensingWaveformConfig,
    build_dmg_sensing_waveform,
    save_dmg_sensing_waveform_npz,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def make_usrp_device_args(serial_or_args: str) -> str:
    x = serial_or_args.strip()

    if not x:
        raise ValueError("Empty serial/device args")

    if "serial=" in x:
        return x

    if "," in x:
        first, rest = x.split(",", 1)
        return f"serial={first},{rest}"

    return f"serial={x}"


def main() -> None:
    p = argparse.ArgumentParser()

    p.add_argument("--serial", required=True, help="USRP serial or UHD device args")

    p.add_argument("--freq", type=float, default=2.412e9)
    p.add_argument("--rate", type=float, default=20e6)
    p.add_argument("--gain", type=float, default=20.0)
    p.add_argument("--antenna", default="TX/RX")
    p.add_argument("--channel", type=int, default=0)

    p.add_argument("--tx-period-ms", type=float, default=100.0)
    p.add_argument("--num-bursts", type=int, default=5000)
    p.add_argument("--start-delay-sec", type=float, default=2.0)
    p.add_argument("--tx-lead-sec", type=float, default=0.020)
    p.add_argument("--progress-every", type=int, default=50)

    p.add_argument("--peak", type=float, default=0.55)
    p.add_argument("--guard-len", type=int, default=256)
    p.add_argument("--taper-len", type=int, default=64)

    p.add_argument("--stf-repetitions", type=int, default=16)
    p.add_argument("--cef-repetitions", type=int, default=1)
    p.add_argument("--header-len", type=int, default=512)

    p.add_argument("--sensing-blocks", type=int, default=16)
    p.add_argument("--sensing-block-len", type=int, default=512)

    p.add_argument("--trn-units", type=int, default=4)
    p.add_argument("--trn-subfields-per-unit", type=int, default=4)

    p.add_argument("--seed", type=int, default=80211)

    p.add_argument("--output-npz", default="results/wlan_sensing_dmg/dmg_like_sensing_ppdu_v1.npz")
    p.add_argument("--json-path", default="results/wlan_sensing_dmg/live_dmg_sensing_tx_state.json")

    p.add_argument("--dry-run", action="store_true")

    args = p.parse_args()

    cfg = DmgSensingWaveformConfig(
        sample_rate_hz=args.rate,
        peak=args.peak,
        guard_len=args.guard_len,
        taper_len=args.taper_len,
        stf_repetitions=args.stf_repetitions,
        cef_repetitions=args.cef_repetitions,
        header_len=args.header_len,
        sensing_blocks=args.sensing_blocks,
        sensing_block_len=args.sensing_block_len,
        trn_units=args.trn_units,
        trn_subfields_per_unit=args.trn_subfields_per_unit,
        seed=args.seed,
    )

    payload = build_dmg_sensing_waveform(cfg)
    save_dmg_sensing_waveform_npz(args.output_npz, payload)

    waveform = payload["waveform"].astype(np.complex64)
    meta = payload["metadata"]

    period_s = args.tx_period_ms / 1000.0
    ppdu_duration_s = len(waveform) / args.rate
    duty_cycle = ppdu_duration_s / period_s if period_s > 0 else 0.0

    base_state = {
        "schema_version": "dmg_like_sensing_tx_state_v1",
        "role": "tx",
        "signal_type": "dmg_80211bf_inspired_sensing_ppdu",
        "profile": "sub6_scaled_dmg_like_test_profile",
        "standard_note": (
            "Waveform-only DMG/802.11bf-inspired sensing signal. "
            "No MAC negotiation, no association, no feedback reporting, "
            "no commercial interoperability."
        ),
        "valid": True,
        "status": "initializing",
        "timestamp_utc": utc_now_iso(),
        "serial": args.serial,
        "freq_hz": args.freq,
        "sample_rate_hz": args.rate,
        "gain_db": args.gain,
        "antenna": args.antenna,
        "channel": args.channel,
        "tx_period_ms": args.tx_period_ms,
        "num_bursts_requested": args.num_bursts,
        "ppdu_samples": int(len(waveform)),
        "ppdu_duration_us": float(ppdu_duration_s * 1e6),
        "duty_cycle": float(duty_cycle),
        "output_npz": args.output_npz,
        "waveform_metadata": meta,
    }

    atomic_write_json(args.json_path, base_state)

    print("DMG-like WLAN sensing TX")
    print(f"  serial: {args.serial}")
    print(f"  antenna: {args.antenna}")
    print(f"  freq: {args.freq}")
    print(f"  rate: {args.rate}")
    print(f"  gain: {args.gain}")
    print(f"  tx period: {args.tx_period_ms} ms")
    print(f"  waveform samples: {len(waveform)}")
    print(f"  waveform duration us: {ppdu_duration_s * 1e6:.3f}")
    print(f"  duty cycle: {duty_cycle * 100:.3f} %")
    print(f"  output npz: {args.output_npz}")
    print(f"  JSON: {args.json_path}")
    print(f"  peak: {np.max(np.abs(waveform)):.4f}")
    print("  fields:")
    for name, fm in meta["field_map"].items():
        print(f"    {name}: {fm['num_samples']} samples")

    if args.dry_run:
        state = dict(base_state)
        state.update(
            {
                "status": "dry_run_complete",
                "timestamp_utc": utc_now_iso(),
                "sent_bursts": 0,
                "total_sent_samples": 0,
                "total_zero_sends": 0,
            }
        )
        atomic_write_json(args.json_path, state)

        print("Dry run complete. No RF transmitted.")
        return

    try:
        import uhd
    except ImportError as e:
        raise SystemExit(
            "Could not import uhd. Activate .venv_uhd created with --system-site-packages."
        ) from e

    device_args = make_usrp_device_args(args.serial)

    usrp = uhd.usrp.MultiUSRP(device_args)

    usrp.set_tx_rate(args.rate, args.channel)
    usrp.set_tx_freq(uhd.types.TuneRequest(args.freq), args.channel)
    usrp.set_tx_gain(args.gain, args.channel)
    usrp.set_tx_antenna(args.antenna, args.channel)

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [args.channel]
    tx_stream = usrp.get_tx_stream(stream_args)

    time.sleep(0.2)

    usrp.set_time_now(uhd.types.TimeSpec(0.0))

    start_time = usrp.get_time_now().get_real_secs() + args.start_delay_sec
    next_time = start_time

    sent_bursts = 0
    total_sent_samples = 0
    total_zero_sends = 0

    state = dict(base_state)
    state.update(
        {
            "status": "transmitting",
            "timestamp_utc": utc_now_iso(),
            "start_time_usrp": float(start_time),
            "sent_bursts": 0,
            "total_sent_samples": 0,
            "total_zero_sends": 0,
        }
    )
    atomic_write_json(args.json_path, state)

    print("Starting timed TX...")
    print(f"  start_time_usrp: {start_time:.6f}")
    print(f"  bursts: {args.num_bursts}")

    try:
        for burst_idx in range(args.num_bursts):
            now = usrp.get_time_now().get_real_secs()

            # Do not schedule too close to current hardware time.
            if next_time < now + args.tx_lead_sec:
                next_time = now + args.tx_lead_sec

            md = uhd.types.TXMetadata()
            md.has_time_spec = True
            md.time_spec = uhd.types.TimeSpec(next_time)
            md.start_of_burst = True
            md.end_of_burst = True

            sent = tx_stream.send(waveform, md)

            if sent == 0:
                total_zero_sends += 1

            sent_bursts += 1
            total_sent_samples += int(sent)

            if args.progress_every > 0 and (
                burst_idx % args.progress_every == 0
                or burst_idx == args.num_bursts - 1
            ):
                now2 = usrp.get_time_now().get_real_secs()

                print(
                    f"burst={burst_idx} "
                    f"scheduled={next_time:.6f} "
                    f"usrp_now={now2:.6f} "
                    f"sent={sent} "
                    f"zero_sends={total_zero_sends}"
                )

                state = dict(base_state)
                state.update(
                    {
                        "status": "transmitting",
                        "timestamp_utc": utc_now_iso(),
                        "last_burst_index": int(burst_idx),
                        "last_scheduled_time_usrp": float(next_time),
                        "sent_bursts": int(sent_bursts),
                        "total_sent_samples": int(total_sent_samples),
                        "total_zero_sends": int(total_zero_sends),
                    }
                )
                atomic_write_json(args.json_path, state)

            next_time += period_s

    except KeyboardInterrupt:
        print("Interrupted by user.")

    finally:
        md = uhd.types.TXMetadata()
        md.start_of_burst = False
        md.end_of_burst = True
        tx_stream.send(np.zeros(0, dtype=np.complex64), md)

        state = dict(base_state)
        state.update(
            {
                "status": "stopped",
                "timestamp_utc": utc_now_iso(),
                "sent_bursts": int(sent_bursts),
                "total_sent_samples": int(total_sent_samples),
                "total_zero_sends": int(total_zero_sends),
            }
        )
        atomic_write_json(args.json_path, state)

        print("TX stopped.")
        print(f"  sent bursts: {sent_bursts}")
        print(f"  total sent samples: {total_sent_samples}")
        print(f"  total zero sends: {total_zero_sends}")


if __name__ == "__main__":
    main()
