#!/usr/bin/env python3
"""
802.11 Beacon MAC frame builder.

Default profile:
  - Beacon management frame
  - ESS + Privacy + Short Slot Time + Radio Measurement capabilities
  - SSID
  - Supported Rates
  - DS Parameter Set
  - TIM
  - Country
  - ERP
  - Extended Supported Rates
  - RSN WPA2-PSK CCMP
  - WMM vendor IE
  - FCS/CRC32 appended
"""

from __future__ import annotations

import argparse
import binascii
import struct
import zlib
from dataclasses import dataclass


def mac_addr_to_bytes(mac: str) -> bytes:
    parts = mac.split(":")
    if len(parts) != 6:
        raise ValueError(f"Invalid MAC address: {mac}")
    return bytes(int(p, 16) for p in parts)


def ie(element_id: int, payload: bytes) -> bytes:
    if not 0 <= element_id <= 255:
        raise ValueError("element_id must fit in one byte")
    if len(payload) > 255:
        raise ValueError("IE payload too long")
    return bytes([element_id, len(payload)]) + payload


def ssid_ie(ssid: str) -> bytes:
    b = ssid.encode("utf-8")
    if len(b) > 32:
        raise ValueError("SSID cannot exceed 32 bytes")
    return ie(0, b)


def supported_rates_ie(router_like_24ghz: bool = True) -> bytes:
    if router_like_24ghz:
        # 1(B), 2(B), 5.5(B), 11(B), 6, 9, 12, 18 Mbps
        rates = bytes([0x82, 0x84, 0x8B, 0x96, 0x0C, 0x12, 0x18, 0x24])
    else:
        # OFDM-only rates
        rates = bytes([0x8C, 0x12, 0x98, 0x24, 0xB0, 0x48, 0x60, 0x6C])
    return ie(1, rates)


def extended_supported_rates_ie() -> bytes:
    # 24, 36, 48, 54 Mbps
    return ie(50, bytes([0x30, 0x48, 0x60, 0x6C]))


def ds_parameter_set_ie(channel: int) -> bytes:
    if not 1 <= channel <= 233:
        raise ValueError("Invalid channel number")
    return ie(3, bytes([channel]))


def tim_ie() -> bytes:
    # DTIM count, DTIM period, bitmap control, partial virtual bitmap.
    return ie(5, bytes([0, 1, 0, 0]))


def country_ie(country: str = "ES", first_channel: int = 1, num_channels: int = 13, max_tx_power_dbm: int = 20) -> bytes:
    country = (country.upper()[:2] + " ")[:3]
    payload = country.encode("ascii") + bytes([first_channel, num_channels, max_tx_power_dbm])
    return ie(7, payload)


def erp_ie() -> bytes:
    # NonERP_Present=0, Use_Protection=0, Barker_Preamble_Mode=0
    return ie(42, bytes([0x00]))


def rsn_wpa2_psk_ccmp_ie() -> bytes:
    payload = bytes.fromhex(
        "0100"
        "000fac04"
        "0100"
        "000fac04"
        "0100"
        "000fac02"
        "0000"
    )
    return ie(48, payload)


def wmm_parameter_ie() -> bytes:
    payload = bytes.fromhex(
        "0050f2"
        "02"
        "01"
        "01"
        "00"
        "00"
        "03a40000"
        "27a40000"
        "42435e00"
        "62322f00"
    )
    return ie(221, payload)


@dataclass
class BeaconConfig:
    ssid: str = "SENSING_WIFI"
    bssid: str = "02:11:22:33:44:55"
    source_mac: str | None = None
    beacon_interval_tu: int = 98
    channel: int = 1
    sequence_number: int = 0
    timestamp_us: int = 0
    profile: str = "router_like_wpa2"
    country: str = "ES"


def capability_info(profile: str) -> int:
    ess = 1 << 0
    privacy = 1 << 4
    short_slot = 1 << 10
    radio_measurement = 1 << 12

    if profile == "minimal_open":
        return ess | short_slot

    if profile == "router_like_wpa2":
        # 0x1411: ESS + Privacy + Short Slot Time + Radio Measurement.
        return ess | privacy | short_slot | radio_measurement

    raise ValueError(f"Unknown beacon profile: {profile}")


def build_beacon_body(cfg: BeaconConfig) -> bytes:
    fixed = struct.pack(
        "<QHH",
        int(cfg.timestamp_us) & 0xFFFFFFFFFFFFFFFF,
        int(cfg.beacon_interval_tu) & 0xFFFF,
        capability_info(cfg.profile),
    )

    tagged = bytearray()
    tagged += ssid_ie(cfg.ssid)
    tagged += supported_rates_ie(router_like_24ghz=True)
    tagged += ds_parameter_set_ie(cfg.channel)
    tagged += tim_ie()

    if cfg.profile == "router_like_wpa2":
        tagged += country_ie(cfg.country, first_channel=1, num_channels=13, max_tx_power_dbm=20)
        tagged += erp_ie()
        tagged += extended_supported_rates_ie()
        tagged += rsn_wpa2_psk_ccmp_ie()
        tagged += wmm_parameter_ie()
    elif cfg.profile == "minimal_open":
        tagged += extended_supported_rates_ie()
    else:
        raise ValueError(f"Unknown beacon profile: {cfg.profile}")

    return fixed + bytes(tagged)


def crc32_fcs(data: bytes) -> bytes:
    return struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF)


def build_beacon_mpdu(cfg: BeaconConfig, include_fcs: bool = True) -> bytes:
    addr1 = b"\xff" * 6
    addr2 = mac_addr_to_bytes(cfg.source_mac or cfg.bssid)
    addr3 = mac_addr_to_bytes(cfg.bssid)

    frame_control = b"\x80\x00"
    duration = b"\x00\x00"
    seq_ctrl = struct.pack("<H", ((cfg.sequence_number & 0xFFF) << 4) | 0)

    mac_header = frame_control + duration + addr1 + addr2 + addr3 + seq_ctrl
    body = build_beacon_body(cfg)

    mpdu_no_fcs = mac_header + body
    if include_fcs:
        return mpdu_no_fcs + crc32_fcs(mpdu_no_fcs)
    return mpdu_no_fcs


def bytes_to_bits_lsb_first(data: bytes) -> list[int]:
    return [(byte >> bit) & 1 for byte in data for bit in range(8)]


def bits_to_bytes_lsb_first(bits: list[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits), 8):
        b = 0
        for j, bit in enumerate(bits[i:i + 8]):
            b |= (int(bit) & 1) << j
        out.append(b)
    return bytes(out)


def describe_beacon(cfg: BeaconConfig) -> str:
    mpdu = build_beacon_mpdu(cfg, include_fcs=True)
    return (
        f"SSID: {cfg.ssid}\n"
        f"BSSID: {cfg.bssid}\n"
        f"Profile: {cfg.profile}\n"
        f"Channel: {cfg.channel}\n"
        f"Beacon Interval field: {cfg.beacon_interval_tu} TU = {cfg.beacon_interval_tu * 1024e-6:.6f} s\n"
        f"Sequence number: {cfg.sequence_number}\n"
        f"Timestamp: {cfg.timestamp_us} us\n"
        f"MPDU length with FCS: {len(mpdu)} bytes\n"
        f"MPDU hex:\n{binascii.hexlify(mpdu).decode()}\n"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ssid", default="SENSING_WIFI")
    p.add_argument("--bssid", default="02:11:22:33:44:55")
    p.add_argument("--source-mac", default=None)
    p.add_argument("--beacon-interval-tu", type=int, default=98)
    p.add_argument("--channel", type=int, default=1)
    p.add_argument("--sequence-number", type=int, default=0)
    p.add_argument("--timestamp-us", type=int, default=0)
    p.add_argument("--profile", choices=["minimal_open", "router_like_wpa2"], default="router_like_wpa2")
    p.add_argument("--country", default="ES")
    p.add_argument("--output-bin", default="")
    args = p.parse_args()

    cfg = BeaconConfig(
        ssid=args.ssid,
        bssid=args.bssid,
        source_mac=args.source_mac,
        beacon_interval_tu=args.beacon_interval_tu,
        channel=args.channel,
        sequence_number=args.sequence_number,
        timestamp_us=args.timestamp_us,
        profile=args.profile,
        country=args.country,
    )

    print(describe_beacon(cfg))

    if args.output_bin:
        with open(args.output_bin, "wb") as f:
            f.write(build_beacon_mpdu(cfg, include_fcs=True))
        print(f"Wrote {args.output_bin}")


if __name__ == "__main__":
    main()
