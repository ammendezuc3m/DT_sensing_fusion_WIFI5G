#!/usr/bin/env python3
"""
Generic WiFi waveform transmitter for USRP B210.

Current mode:
  --mode beacon   / --beacon

Reserved mode:
  --mode bf       / --bf

Design:
  packet builder -> complex baseband waveform -> common UHD TX engine

Beacon defaults:
  - 802.11 legacy OFDM, 20 MHz
  - 6 Mb/s, BPSK 1/2 (implemented by wifi_legacy_ofdm.make_beacon_waveform)
  - Beacon interval = 100 TU = 102.4 ms
  - Vendor-specific IE for unambiguous experiment identification
  - RF frequency and advertised WiFi channel must agree

Important:
  The existing make_beacon_waveform() must support either:
      extra_ies=[bytes(...)]
  or:
      vendor_ie=bytes(...)
  so that the vendor-specific IE is inserted before the FCS is calculated.
  This transmitter checks the function signature and fails fast if the helper
  has not yet been extended.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    import uhd
except ImportError:
    uhd = None

try:
    from .wifi_legacy_ofdm import SAMPLE_RATE, make_beacon_waveform
except ImportError:
    from wifi_legacy_ofdm import SAMPLE_RATE, make_beacon_waveform


WIFI_24_GHZ_CHANNELS_HZ = {
    ch: int(2.412e9 + (ch - 1) * 5e6)
    for ch in range(1, 14)
}
WIFI_24_GHZ_CHANNELS_HZ[14] = int(2.484e9)

TU_SECONDS = 1024e-6
DEFAULT_BEACON_INTERVAL_TU = 100
DEFAULT_SAMPLE_RATE = 20e6


def utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=path.name + ".",
        suffix=".tmp",
    ) as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)

    os.replace(tmp_path, path)


def parse_hex_bytes(value: str, expected_len: int | None = None) -> bytes:
    normalized = value.replace(":", "").replace("-", "").replace(" ", "")
    if len(normalized) % 2:
        raise argparse.ArgumentTypeError("Hex value must contain a whole number of bytes.")
    try:
        data = bytes.fromhex(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid hex value: {value}") from exc

    if expected_len is not None and len(data) != expected_len:
        raise argparse.ArgumentTypeError(
            f"Expected exactly {expected_len} bytes, got {len(data)}."
        )
    return data


def validate_mac_address(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("MAC address must have 6 colon-separated bytes.")
    try:
        octets = [int(part, 16) for part in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Invalid MAC address.") from exc
    if any(not 0 <= octet <= 255 for octet in octets):
        raise argparse.ArgumentTypeError("Invalid MAC address.")
    return ":".join(f"{octet:02x}" for octet in octets)


def expected_freq_for_channel(channel: int) -> int:
    try:
        return WIFI_24_GHZ_CHANNELS_HZ[channel]
    except KeyError as exc:
        raise ValueError("Only 2.4 GHz WiFi channels 1..14 are supported here.") from exc


def validate_rf_channel_consistency(freq_hz: float, wifi_channel: int, tolerance_hz: float = 1.0) -> None:
    expected = expected_freq_for_channel(wifi_channel)
    error = abs(float(freq_hz) - float(expected))
    if error > tolerance_hz:
        raise SystemExit(
            "RF/channel inconsistency:\n"
            f"  --freq         = {freq_hz / 1e6:.3f} MHz\n"
            f"  --wifi-channel = {wifi_channel}\n"
            f"  expected       = {expected / 1e6:.3f} MHz\n"
            "Set both values consistently."
        )


def build_vendor_specific_ie(
    oui: bytes,
    vendor_type: int,
    magic: bytes,
    version: int,
    transmitter_id: int,
    experiment_id: int,
    packet_counter: int,
) -> bytes:
    """
    Build a complete IEEE 802.11 Vendor Specific IE:

      Element ID = 221 (0xDD)
      Length
      OUI[3]
      Vendor type[1]
      Magic[8]
      Version[1]
      Transmitter ID[2]
      Experiment ID[2]
      Packet counter[4]

    Total payload length = 21 bytes.
    """
    if len(oui) != 3:
        raise ValueError("OUI must be exactly 3 bytes.")
    if len(magic) > 8:
        raise ValueError("Magic must contain at most 8 bytes.")
    if not 0 <= vendor_type <= 255:
        raise ValueError("vendor_type must fit in uint8.")
    if not 0 <= version <= 255:
        raise ValueError("version must fit in uint8.")
    if not 0 <= transmitter_id <= 0xFFFF:
        raise ValueError("transmitter_id must fit in uint16.")
    if not 0 <= experiment_id <= 0xFFFF:
        raise ValueError("experiment_id must fit in uint16.")
    if not 0 <= packet_counter <= 0xFFFFFFFF:
        raise ValueError("packet_counter must fit in uint32.")

    magic_padded = magic.ljust(8, b"\x00")

    payload = (
        oui
        + bytes([vendor_type])
        + magic_padded
        + bytes([version])
        + transmitter_id.to_bytes(2, "big")
        + experiment_id.to_bytes(2, "big")
        + packet_counter.to_bytes(4, "big")
    )

    if len(payload) > 255:
        raise ValueError("Vendor IE payload exceeds 255 bytes.")

    return bytes([221, len(payload)]) + payload


@dataclass(frozen=True)
class BuiltPacket:
    waveform: np.ndarray
    mpdu: bytes
    metadata: dict[str, Any]


class PacketBuilder(ABC):
    mode_name: str

    @property
    @abstractmethod
    def nominal_period_s(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def build(self, packet_index: int) -> BuiltPacket:
        raise NotImplementedError

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


class BeaconPacketBuilder(PacketBuilder):
    mode_name = "beacon"

    def __init__(
        self,
        *,
        ssid: str,
        bssid: str,
        wifi_channel: int,
        profile: str,
        beacon_interval_tu: int,
        amplitude: float,
        vendor_oui: bytes,
        vendor_type: int,
        vendor_magic: bytes,
        vendor_version: int,
        transmitter_id: int,
        experiment_id: int,
    ) -> None:
        self.ssid = ssid
        self.bssid = bssid
        self.wifi_channel = wifi_channel
        self.profile = profile
        self.beacon_interval_tu = beacon_interval_tu
        self.amplitude = amplitude
        self.vendor_oui = vendor_oui
        self.vendor_type = vendor_type
        self.vendor_magic = vendor_magic
        self.vendor_version = vendor_version
        self.transmitter_id = transmitter_id
        self.experiment_id = experiment_id

        if beacon_interval_tu <= 0:
            raise ValueError("Beacon interval must be positive.")
        if not 0 < amplitude <= 1.0:
            raise ValueError("Amplitude must be in (0, 1].")

        self._beacon_signature = inspect.signature(make_beacon_waveform)
        params = self._beacon_signature.parameters
        if "extra_ies" in params:
            self._vendor_argument_name = "extra_ies"
        elif "vendor_ie" in params:
            self._vendor_argument_name = "vendor_ie"
        else:
            raise RuntimeError(
                "make_beacon_waveform() does not yet support Vendor IE insertion.\n"
                "Extend it with either an `extra_ies` argument containing complete IE byte strings,\n"
                "or a `vendor_ie` argument containing one complete Vendor Specific IE.\n"
                "The IE must be inserted before the MPDU FCS is calculated."
            )

    @property
    def nominal_period_s(self) -> float:
        return self.beacon_interval_tu * TU_SECONDS

    def _scale_waveform(self, waveform: np.ndarray) -> np.ndarray:
        waveform = np.asarray(waveform, dtype=np.complex64)
        peak = float(np.max(np.abs(waveform))) if waveform.size else 0.0
        if peak <= 0.0:
            raise RuntimeError("Generated waveform is empty or has zero amplitude.")
        return (waveform * (self.amplitude / peak)).astype(np.complex64, copy=False)

    def build(self, packet_index: int) -> BuiltPacket:
        sequence_number = packet_index & 0xFFF
        timestamp_us = int(round(packet_index * self.nominal_period_s * 1e6))

        vendor_ie = build_vendor_specific_ie(
            oui=self.vendor_oui,
            vendor_type=self.vendor_type,
            magic=self.vendor_magic,
            version=self.vendor_version,
            transmitter_id=self.transmitter_id,
            experiment_id=self.experiment_id,
            packet_counter=packet_index & 0xFFFFFFFF,
        )

        kwargs: dict[str, Any] = {
            "ssid": self.ssid,
            "bssid": self.bssid,
            "channel": self.wifi_channel,
            "beacon_interval_tu": self.beacon_interval_tu,
            "sequence_number": sequence_number,
            "timestamp_us": timestamp_us,
            "profile": self.profile,
        }

        if self._vendor_argument_name == "extra_ies":
            kwargs["extra_ies"] = [vendor_ie]
        else:
            kwargs["vendor_ie"] = vendor_ie

        waveform, mpdu = make_beacon_waveform(**kwargs)
        waveform = self._scale_waveform(waveform)

        return BuiltPacket(
            waveform=waveform,
            mpdu=bytes(mpdu),
            metadata={
                "packet_type": "beacon",
                "sequence_number": sequence_number,
                "timestamp_us": timestamp_us,
                "vendor_packet_counter": packet_index & 0xFFFFFFFF,
                "vendor_ie_hex": vendor_ie.hex(),
            },
        )

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode_name,
            "phy": "802.11 legacy OFDM 20 MHz",
            "rate": "6 Mb/s BPSK 1/2",
            "ssid": self.ssid,
            "bssid": self.bssid,
            "wifi_channel": self.wifi_channel,
            "profile": self.profile,
            "beacon_interval_tu": self.beacon_interval_tu,
            "period_ms": self.nominal_period_s * 1e3,
            "vendor_oui": self.vendor_oui.hex(":"),
            "vendor_type": self.vendor_type,
            "vendor_magic": self.vendor_magic.decode("ascii", errors="replace"),
            "vendor_version": self.vendor_version,
            "transmitter_id": self.transmitter_id,
            "experiment_id": self.experiment_id,
        }


class BeamformingPacketBuilder(PacketBuilder):
    mode_name = "bf"

    def __init__(self, **_: Any) -> None:
        raise NotImplementedError(
            "Beamforming mode is intentionally reserved but not fabricated here. "
            "Its PHY/MAC format, number of TX chains, calibration and timing must be "
            "defined before implementing the packet builder."
        )

    @property
    def nominal_period_s(self) -> float:
        raise NotImplementedError

    def build(self, packet_index: int) -> BuiltPacket:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


def build_packet_builder(args: argparse.Namespace) -> PacketBuilder:
    if args.mode == "beacon":
        return BeaconPacketBuilder(
            ssid=args.ssid,
            bssid=args.bssid,
            wifi_channel=args.wifi_channel,
            profile=args.profile,
            beacon_interval_tu=args.beacon_interval_tu,
            amplitude=args.amplitude,
            vendor_oui=args.vendor_oui,
            vendor_type=args.vendor_type,
            vendor_magic=args.vendor_magic.encode("ascii"),
            vendor_version=args.vendor_version,
            transmitter_id=args.transmitter_id,
            experiment_id=args.experiment_id,
        )
    if args.mode == "bf":
        return BeamformingPacketBuilder()
    raise ValueError(f"Unsupported mode: {args.mode}")


def wait_until_usrp_time(usrp: Any, target_time: float, lead_sec: float) -> float:
    while True:
        now = usrp.get_time_now().get_real_secs()
        remaining = target_time - lead_sec - now
        if remaining <= 0:
            return now
        time.sleep(min(remaining, 0.050))


def send_waveform_at(
    tx_streamer: Any,
    waveform: np.ndarray,
    tx_time: float,
    max_zero_sends: int = 50,
) -> tuple[int, int]:
    md = uhd.types.TXMetadata()
    md.has_time_spec = True
    md.time_spec = uhd.types.TimeSpec(float(tx_time))
    md.start_of_burst = True
    md.end_of_burst = False

    max_samps = tx_streamer.get_max_num_samps()
    offset = 0
    zero_send_count = 0
    consecutive_zero_sends = 0

    while offset < waveform.size:
        chunk = waveform[offset: offset + max_samps]
        is_last = offset + chunk.size >= waveform.size
        md.end_of_burst = is_last

        sent = tx_streamer.send(chunk, md)

        if sent == 0:
            zero_send_count += 1
            consecutive_zero_sends += 1
            if consecutive_zero_sends > max_zero_sends:
                raise RuntimeError(
                    f"UHD TX send returned 0 more than {max_zero_sends} consecutive times."
                )
            time.sleep(0.001)
            continue

        if sent < 0:
            raise RuntimeError(f"UHD TX send returned an invalid count: {sent}")

        offset += sent
        consecutive_zero_sends = 0
        md.has_time_spec = False
        md.start_of_burst = False

    return offset, zero_send_count


def poll_async_tx_events(tx_streamer: Any, timeout_s: float = 0.0) -> list[str]:
    """
    Drain UHD asynchronous TX metadata when supported by the installed UHD binding.
    Typical events include burst ACK, underflow, time error and sequence error.
    """
    events: list[str] = []

    if uhd is None or not hasattr(tx_streamer, "recv_async_msg"):
        return events

    async_md = uhd.types.TXAsyncMetadata()

    while True:
        try:
            received = tx_streamer.recv_async_msg(async_md, timeout_s)
        except TypeError:
            # Some UHD Python versions accept only the metadata argument.
            received = tx_streamer.recv_async_msg(async_md)
        except Exception as exc:
            events.append(f"async_metadata_error:{exc!r}")
            break

        if not received:
            break

        event_code = getattr(async_md, "event_code", None)
        events.append(str(event_code))
        timeout_s = 0.0

    return events


def resolve_mode(args: argparse.Namespace) -> str:
    aliases = int(bool(args.beacon)) + int(bool(args.bf))
    if aliases > 1:
        raise SystemExit("Use only one of --beacon or --bf.")

    if args.beacon:
        return "beacon"
    if args.bf:
        return "bf"
    return args.mode


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generic packet-mode WiFi transmitter for USRP."
    )

    p.add_argument("--mode", choices=["beacon", "bf"], default="beacon")
    p.add_argument("--beacon", action="store_true", help="Alias for --mode beacon.")
    p.add_argument("--bf", action="store_true", help="Alias for --mode bf.")

    p.add_argument("--serial", default="")
    p.add_argument("--freq", type=float, default=2.412e9)
    p.add_argument("--rate", type=float, default=DEFAULT_SAMPLE_RATE)
    p.add_argument("--gain", type=float, default=20.0)
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--antenna", default="TX/RX")
    p.add_argument("--bandwidth", type=float, default=20e6)

    p.add_argument("--ssid", default="SENSING_WIFI")
    p.add_argument("--bssid", type=validate_mac_address, default="02:11:22:33:44:55")
    p.add_argument("--wifi-channel", type=int, default=1)
    p.add_argument(
        "--profile",
        choices=["minimal", "router_like_wpa2"],
        default="router_like_wpa2",
    )
    p.add_argument(
        "--beacon-interval-tu",
        type=int,
        default=DEFAULT_BEACON_INTERVAL_TU,
        help="100 TU corresponds to 102.4 ms.",
    )

    p.add_argument("--vendor-oui", type=lambda x: parse_hex_bytes(x, 3), default=b"\x02\x11\x22")
    p.add_argument("--vendor-type", type=int, default=1)
    p.add_argument("--vendor-magic", default="ALBSENS")
    p.add_argument("--vendor-version", type=int, default=1)
    p.add_argument("--transmitter-id", type=int, default=1)
    p.add_argument("--experiment-id", type=int, default=1)

    p.add_argument("--num-packets", "--num-beacons", dest="num_packets", type=int, default=5000)
    p.add_argument("--start-delay-sec", type=float, default=2.0)
    p.add_argument("--tx-lead-sec", type=float, default=0.020)
    p.add_argument("--amplitude", type=float, default=0.55)

    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--save-first-npz",
        default="results/wifi_debug/first_tx_packet.npz",
    )
    p.add_argument(
        "--local-json",
        default="results/wifi_online/live_wifi_tx_state.json",
    )
    p.add_argument("--progress-every", type=int, default=10)

    return p


def main() -> None:
    parser = make_parser()
    args = parser.parse_args()
    args.mode = resolve_mode(args)

    if abs(args.rate - SAMPLE_RATE) > 1.0:
        raise SystemExit(
            f"This implementation requires {SAMPLE_RATE:.0f} samples/s."
        )
    if args.tx_lead_sec <= 0:
        raise SystemExit("--tx-lead-sec must be > 0.")
    if args.start_delay_sec <= args.tx_lead_sec:
        raise SystemExit("--start-delay-sec should exceed --tx-lead-sec.")
    if args.num_packets <= 0:
        raise SystemExit("--num-packets must be > 0.")
    if len(args.vendor_magic.encode("ascii")) > 8:
        raise SystemExit("--vendor-magic must contain at most 8 ASCII bytes.")

    validate_rf_channel_consistency(args.freq, args.wifi_channel)

    builder = build_packet_builder(args)
    first = builder.build(0)

    period_s = builder.nominal_period_s
    if not math.isclose(
        period_s,
        args.beacon_interval_tu * TU_SECONDS,
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        raise RuntimeError("Internal packet period inconsistency.")

    save_path = Path(args.save_first_npz)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        save_path,
        waveform=first.waveform,
        mpdu=np.frombuffer(first.mpdu, dtype=np.uint8),
        sample_rate_hz=np.array([args.rate], dtype=np.float64),
        metadata_json=np.array([json.dumps(first.metadata)]),
    )

    description = builder.describe()

    print("WiFi USRP TX configuration")
    for key, value in description.items():
        print(f"  {key}: {value}")
    print(f"  RF frequency: {args.freq / 1e6:.3f} MHz")
    print(f"  sample rate: {args.rate / 1e6:.3f} Msps")
    print(f"  TX gain: {args.gain:.1f} dB")
    print(f"  amplitude peak target: {args.amplitude:.3f}")
    print(f"  first waveform samples: {first.waveform.size}")
    print(f"  first waveform duration: {first.waveform.size / args.rate * 1e6:.3f} us")
    print(f"  first MPDU bytes: {len(first.mpdu)}")
    print(f"  first packet NPZ: {save_path}")

    base_state: dict[str, Any] = {
        "schema_version": "wifi_tx_v2",
        "role": "tx",
        "mode": args.mode,
        "valid": True,
        "error": None,
        "freq_hz": args.freq,
        "sample_rate_hz": args.rate,
        "gain_db": args.gain,
        "radio_channel": args.channel,
        "antenna": args.antenna,
        "bandwidth_hz": args.bandwidth,
        "period_s": period_s,
        "period_ms": period_s * 1e3,
        "num_packets_requested": args.num_packets,
        "packet_builder": description,
        "ppdu_samples": int(first.waveform.size),
        "ppdu_duration_us": float(first.waveform.size / args.rate * 1e6),
        "mpdu_bytes": len(first.mpdu),
    }

    if args.dry_run:
        state = dict(base_state)
        state.update(
            timestamp_utc=utc_now_iso(),
            status="dry_run",
            first_packet_metadata=first.metadata,
        )
        atomic_write_json(args.local_json, state)
        print("Dry run completed; no RF transmitted.")
        return

    if uhd is None:
        raise SystemExit(
            "Python package 'uhd' is unavailable. Activate the UHD virtual environment."
        )

    device_args = f"serial={args.serial}" if args.serial else ""
    usrp = uhd.usrp.MultiUSRP(device_args)

    usrp.set_tx_rate(args.rate, args.channel)
    usrp.set_tx_freq(uhd.types.TuneRequest(args.freq), args.channel)
    usrp.set_tx_gain(args.gain, args.channel)

    if hasattr(usrp, "set_tx_bandwidth"):
        usrp.set_tx_bandwidth(args.bandwidth, args.channel)

    if args.antenna:
        usrp.set_tx_antenna(args.antenna, args.channel)

    actual_rate = float(usrp.get_tx_rate(args.channel))
    actual_freq = float(usrp.get_tx_freq(args.channel))
    actual_gain = float(usrp.get_tx_gain(args.channel))
    actual_antenna = str(usrp.get_tx_antenna(args.channel))

    if abs(actual_rate - args.rate) > 1.0:
        raise RuntimeError(f"Actual TX rate differs: requested={args.rate}, actual={actual_rate}.")
    if abs(actual_freq - args.freq) > 1e3:
        raise RuntimeError(f"Actual TX frequency differs: requested={args.freq}, actual={actual_freq}.")

    print("USRP applied settings")
    print(f"  actual TX rate: {actual_rate / 1e6:.6f} Msps")
    print(f"  actual TX frequency: {actual_freq / 1e6:.6f} MHz")
    print(f"  actual TX gain: {actual_gain:.2f} dB")
    print(f"  actual TX antenna: {actual_antenna}")

    usrp.set_time_now(uhd.types.TimeSpec(0.0))
    time.sleep(0.1)

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [args.channel]
    tx_streamer = usrp.get_tx_stream(stream_args)

    start_time = usrp.get_time_now().get_real_secs() + args.start_delay_sec

    print(f"USRP time now: {usrp.get_time_now().get_real_secs():.6f} s")
    print(f"First packet scheduled at: {start_time:.6f} s")
    print("Transmitting; press Ctrl+C to stop.")

    sent_packets = 0
    total_sent_samples = 0
    total_zero_sends = 0
    async_event_counts: dict[str, int] = {}
    last_error: str | None = None

    try:
        for packet_index in range(args.num_packets):
            tx_time = start_time + packet_index * period_s
            built = builder.build(packet_index)

            before_send = wait_until_usrp_time(usrp, tx_time, args.tx_lead_sec)
            late_by_s = before_send - tx_time
            if late_by_s > 0:
                print(
                    f"WARNING packet={packet_index} late by {late_by_s * 1e3:.3f} ms",
                    file=sys.stderr,
                )

            sent, zero_sends = send_waveform_at(
                tx_streamer=tx_streamer,
                waveform=built.waveform,
                tx_time=tx_time,
            )

            sent_packets += 1
            total_sent_samples += sent
            total_zero_sends += zero_sends

            async_events = poll_async_tx_events(tx_streamer, timeout_s=0.0)
            for event in async_events:
                async_event_counts[event] = async_event_counts.get(event, 0) + 1

            now = usrp.get_time_now().get_real_secs()

            if args.progress_every > 0 and packet_index % args.progress_every == 0:
                print(
                    f"packet={packet_index} "
                    f"scheduled={tx_time:.6f} "
                    f"usrp_now={now:.6f} "
                    f"sent={sent} "
                    f"zero_sends={zero_sends} "
                    f"async={async_events or 'none'}"
                )

            state = dict(base_state)
            state.update(
                timestamp_utc=utc_now_iso(),
                status="transmitting",
                last_packet_index=packet_index,
                last_packet_metadata=built.metadata,
                last_scheduled_usrp_time=tx_time,
                usrp_time_now=now,
                last_sent_samples=sent,
                total_sent_samples=total_sent_samples,
                total_zero_sends=total_zero_sends,
                sent_packets=sent_packets,
                async_event_counts=async_event_counts,
                last_error=None,
            )
            atomic_write_json(args.local_json, state)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        last_error = "KeyboardInterrupt"

    except Exception as exc:
        last_error = repr(exc)
        state = dict(base_state)
        state.update(
            timestamp_utc=utc_now_iso(),
            status="error",
            valid=False,
            error=last_error,
            sent_packets=sent_packets,
            total_sent_samples=total_sent_samples,
            total_zero_sends=total_zero_sends,
            async_event_counts=async_event_counts,
        )
        atomic_write_json(args.local_json, state)
        raise

    finally:
        state = dict(base_state)
        state.update(
            timestamp_utc=utc_now_iso(),
            status="stopped",
            valid=last_error is None or last_error == "KeyboardInterrupt",
            error=last_error,
            sent_packets=sent_packets,
            total_sent_samples=total_sent_samples,
            total_zero_sends=total_zero_sends,
            async_event_counts=async_event_counts,
            usrp_time_now=usrp.get_time_now().get_real_secs(),
        )
        atomic_write_json(args.local_json, state)

    print("TX stopped.")
    print(f"Sent packets: {sent_packets}")
    print(f"Total sent samples: {total_sent_samples}")
    print(f"Total zero sends: {total_zero_sends}")
    print(f"Async TX events: {async_event_counts or 'none'}")
    print(f"Online JSON: {args.local_json}")


if __name__ == "__main__":
    main()
