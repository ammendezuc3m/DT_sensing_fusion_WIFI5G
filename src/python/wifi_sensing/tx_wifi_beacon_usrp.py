#!/usr/bin/env python3
"""
Transmit real 802.11a/g legacy OFDM Beacon frames with a USRP.

V1:
  - 20 MHz sample rate
  - legacy OFDM non-HT
  - 6 Mb/s BPSK 1/2
  - real Beacon MAC frame
  - router_like_wpa2 profile by default
  - physical TX period defaults to 100 ms
  - Beacon Interval field defaults to 98 TU
  - UHD timed TX
  - online TX JSON state
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

try:
    import uhd
except ImportError:
    uhd = None

try:
    from .wifi_legacy_ofdm import SAMPLE_RATE, make_beacon_waveform
    from .wifi_live_json import atomic_write_json, utc_now_iso
except ImportError:
    from wifi_legacy_ofdm import SAMPLE_RATE, make_beacon_waveform
    from wifi_live_json import atomic_write_json, utc_now_iso


def build_usrp_args(serial: str) -> str:
    return f"serial={serial}" if serial else ""


def wait_until_usrp_time(usrp, target_time: float, lead_sec: float) -> float:
    """
    Wait until USRP time is close enough to the scheduled TX time.

    We do not want to enqueue bursts seconds in advance. We enqueue them
    only lead_sec before the target time.
    """
    while True:
        now = usrp.get_time_now().get_real_secs()
        remaining = target_time - lead_sec - now

        if remaining <= 0:
            return now

        time.sleep(min(remaining, 0.050))


def send_waveform_at(tx_streamer, waveform: np.ndarray, tx_time: float, max_zero_sends: int = 50) -> tuple[int, int]:
    """
    Send a waveform as one timed burst.

    Returns:
      total_sent_samples, zero_send_count
    """
    md = uhd.types.TXMetadata()
    md.has_time_spec = True
    md.time_spec = uhd.types.TimeSpec(float(tx_time))
    md.start_of_burst = True
    md.end_of_burst = False

    max_samps = tx_streamer.get_max_num_samps()
    offset = 0
    n = len(waveform)
    zero_send_count = 0
    consecutive_zero_sends = 0

    while offset < n:
        chunk = waveform[offset:offset + max_samps]
        last = offset + len(chunk) >= n

        md.end_of_burst = last

        sent = tx_streamer.send(chunk.astype(np.complex64), md)

        if sent == 0:
            zero_send_count += 1
            consecutive_zero_sends += 1

            if consecutive_zero_sends > max_zero_sends:
                raise RuntimeError(
                    f"UHD TX send returned 0 samples more than {max_zero_sends} consecutive times"
                )

            time.sleep(0.001)
            continue

        if sent < 0:
            raise RuntimeError(f"UHD TX send returned negative sample count: {sent}")

        if sent != len(chunk):
            print(f"WARNING: requested {len(chunk)} samples, sent {sent}", file=sys.stderr)

        consecutive_zero_sends = 0
        offset += sent

        md.has_time_spec = False
        md.start_of_burst = False

    return offset, zero_send_count


def write_tx_state(path: str, payload: dict) -> None:
    if not path:
        return

    atomic_write_json(path, payload)


def main() -> None:
    p = argparse.ArgumentParser()

    p.add_argument("--serial", default="")
    p.add_argument("--freq", type=float, default=2.412e9)
    p.add_argument("--rate", type=float, default=SAMPLE_RATE)
    p.add_argument("--gain", type=float, default=15.0)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="")

    p.add_argument("--ssid", default="SENSING_WIFI")
    p.add_argument("--bssid", default="02:11:22:33:44:55")
    p.add_argument("--wifi-channel", type=int, default=1)
    p.add_argument("--profile", choices=["minimal_open", "router_like_wpa2"], default="router_like_wpa2")

    p.add_argument("--tx-period-ms", type=float, default=100.0)
    p.add_argument("--beacon-interval-tu", type=int, default=98)
    p.add_argument("--num-beacons", type=int, default=1000)
    p.add_argument("--start-delay-sec", type=float, default=2.0)
    p.add_argument("--tx-lead-sec", type=float, default=0.020)
    p.add_argument("--amplitude", type=float, default=0.55)

    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--save-first-npz", default="results/wifi_debug/first_tx_beacon.npz")
    p.add_argument("--local-json", default="results/wifi_online/live_wifi_tx_state.json")
    p.add_argument("--progress-every", type=int, default=10)

    args = p.parse_args()

    if abs(args.rate - SAMPLE_RATE) > 1:
        raise SystemExit("This first implementation expects --rate 20e6.")

    if args.tx_lead_sec <= 0:
        raise SystemExit("--tx-lead-sec must be > 0")

    period_s = args.tx_period_ms / 1000.0

    print("WiFi Beacon TX configuration")
    print("  PHY: legacy OFDM 20 MHz, 6 Mb/s, BPSK 1/2")
    print(f"  SSID: {args.ssid}")
    print(f"  BSSID: {args.bssid}")
    print(f"  TX period: {args.tx_period_ms:.3f} ms")
    print(f"  Beacon Interval field: {args.beacon_interval_tu} TU = {args.beacon_interval_tu * 1024e-3:.3f} ms")
    print(f"  Profile: {args.profile}")
    print(f"  TX lead time: {args.tx_lead_sec * 1e3:.1f} ms")
    print(f"  Online JSON: {args.local_json}")

    first_wf, first_mpdu = make_beacon_waveform(
        ssid=args.ssid,
        bssid=args.bssid,
        channel=args.wifi_channel,
        beacon_interval_tu=args.beacon_interval_tu,
        sequence_number=0,
        timestamp_us=0,
        profile=args.profile,
    )

    if args.amplitude != 0.55:
        peak = np.max(np.abs(first_wf))
        if peak > 0:
            first_wf = (args.amplitude * first_wf / peak).astype(np.complex64)

    save_path = Path(args.save_first_npz)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        save_path,
        waveform=first_wf,
        mpdu=np.frombuffer(first_mpdu, dtype=np.uint8),
        sample_rate_hz=np.array([args.rate], dtype=np.float64),
    )

    print(f"Saved first beacon waveform debug file: {save_path}")
    print(f"First beacon samples: {len(first_wf)}")
    print(f"First beacon duration: {len(first_wf) / args.rate * 1e6:.3f} us")
    print(f"First MPDU length: {len(first_mpdu)} bytes")

    base_state = {
        "schema_version": "wifi_beacon_tx_v1",
        "role": "tx",
        "valid": True,
        "error": None,
        "ssid": args.ssid,
        "bssid": args.bssid,
        "profile": args.profile,
        "freq_hz": args.freq,
        "sample_rate_hz": args.rate,
        "gain_db": args.gain,
        "wifi_channel": args.wifi_channel,
        "tx_period_ms": args.tx_period_ms,
        "beacon_interval_tu": args.beacon_interval_tu,
        "beacon_interval_field_ms": args.beacon_interval_tu * 1.024,
        "ppdu_samples": len(first_wf),
        "ppdu_duration_us": len(first_wf) / args.rate * 1e6,
        "mpdu_bytes": len(first_mpdu),
        "num_beacons_requested": args.num_beacons,
    }

    if args.dry_run:
        state = dict(base_state)
        state.update({
            "timestamp_utc": utc_now_iso(),
            "status": "dry_run",
            "last_beacon_index": None,
            "last_scheduled_usrp_time": None,
        })
        write_tx_state(args.local_json, state)
        print("Dry run: not transmitting.")
        return

    if uhd is None:
        raise SystemExit("Python package 'uhd' is not available. Activate .venv_uhd or install UHD Python bindings.")

    usrp = uhd.usrp.MultiUSRP(build_usrp_args(args.serial))
    usrp.set_tx_rate(args.rate, args.channel)
    usrp.set_tx_freq(uhd.types.TuneRequest(args.freq), args.channel)
    usrp.set_tx_gain(args.gain, args.channel)

    if args.antenna:
        usrp.set_tx_antenna(args.antenna, args.channel)

    usrp.set_time_now(uhd.types.TimeSpec(0.0))
    time.sleep(0.1)

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [args.channel]
    tx_streamer = usrp.get_tx_stream(stream_args)

    start_time = usrp.get_time_now().get_real_secs() + args.start_delay_sec

    print(f"USRP time now: {usrp.get_time_now().get_real_secs():.6f} s")
    print(f"First TX scheduled at USRP time: {start_time:.6f} s")
    print("Transmitting... Ctrl+C to stop.")

    total_zero_sends = 0
    total_sent_samples = 0
    last_error = None
    sent_beacons = 0

    try:
        for i in range(args.num_beacons):
            tsf_us = int(round(i * args.tx_period_ms * 1000.0))
            tx_time = start_time + i * period_s

            wf, mpdu = make_beacon_waveform(
                ssid=args.ssid,
                bssid=args.bssid,
                channel=args.wifi_channel,
                beacon_interval_tu=args.beacon_interval_tu,
                sequence_number=i,
                timestamp_us=tsf_us,
                profile=args.profile,
            )

            if args.amplitude != 0.55:
                peak = np.max(np.abs(wf))
                if peak > 0:
                    wf = (args.amplitude * wf / peak).astype(np.complex64)

            before_send_time = wait_until_usrp_time(usrp, tx_time, args.tx_lead_sec)
            late_by = before_send_time - tx_time

            if late_by > 0:
                print(f"WARNING: beacon={i} is late by {late_by * 1e3:.3f} ms", file=sys.stderr)

            sent, zeros = send_waveform_at(tx_streamer, wf, tx_time)

            total_sent_samples += sent
            total_zero_sends += zeros
            sent_beacons += 1

            now = usrp.get_time_now().get_real_secs()

            if args.progress_every > 0 and i % args.progress_every == 0:
                print(
                    f"beacon={i} "
                    f"scheduled={tx_time:.6f} "
                    f"usrp_now={now:.6f} "
                    f"sent={sent} "
                    f"zero_sends={zeros}"
                )

            state = dict(base_state)
            state.update({
                "timestamp_utc": utc_now_iso(),
                "status": "transmitting",
                "last_beacon_index": i,
                "last_sequence_number": i & 0xFFF,
                "last_tsf_us": tsf_us,
                "last_scheduled_usrp_time": tx_time,
                "usrp_time_now": now,
                "last_sent_samples": sent,
                "total_sent_samples": total_sent_samples,
                "total_zero_sends": total_zero_sends,
                "sent_beacons": sent_beacons,
                "last_error": None,
            })
            write_tx_state(args.local_json, state)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        last_error = "KeyboardInterrupt"

    except Exception as e:
        last_error = repr(e)
        state = dict(base_state)
        state.update({
            "timestamp_utc": utc_now_iso(),
            "status": "error",
            "valid": False,
            "error": last_error,
            "sent_beacons": sent_beacons,
            "total_sent_samples": total_sent_samples,
            "total_zero_sends": total_zero_sends,
        })
        write_tx_state(args.local_json, state)
        raise

    finally:
        state = dict(base_state)
        state.update({
            "timestamp_utc": utc_now_iso(),
            "status": "stopped",
            "valid": last_error is None or last_error == "KeyboardInterrupt",
            "error": last_error,
            "sent_beacons": sent_beacons,
            "total_sent_samples": total_sent_samples,
            "total_zero_sends": total_zero_sends,
            "usrp_time_now": usrp.get_time_now().get_real_secs() if uhd is not None else None,
        })
        write_tx_state(args.local_json, state)

    print("TX stopped.")
    print(f"Sent beacons: {sent_beacons}")
    print(f"Total sent samples: {total_sent_samples}")
    print(f"Total zero sends: {total_zero_sends}")
    print(f"Online JSON: {args.local_json}")


if __name__ == "__main__":
    main()
