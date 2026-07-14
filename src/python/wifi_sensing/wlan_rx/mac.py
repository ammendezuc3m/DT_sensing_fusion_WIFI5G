from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


@dataclass
class VendorInfo:
    valid: bool
    packet_counter: int | None
    transmitter_id: int | None
    experiment_id: int | None
    reason: str


@dataclass
class BeaconInfo:
    valid: bool
    fcs_valid: bool
    is_beacon: bool
    ssid: str | None
    source: str | None
    bssid: str | None
    sequence_number: int | None
    beacon_interval_tu: int | None
    vendor: VendorInfo
    reason: str


def mac_string(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def parse_ies(body: bytes, start: int = 12) -> list[tuple[int, bytes]]:
    out = []
    pos = start
    while pos + 2 <= len(body):
        eid = body[pos]
        length = body[pos + 1]
        pos += 2
        if pos + length > len(body):
            break
        out.append((eid, body[pos:pos + length]))
        pos += length
    return out


def parse_vendor(
    ies: list[tuple[int, bytes]],
    *,
    expected_oui: bytes,
    expected_type: int,
    expected_magic: bytes,
    expected_version: int,
    expected_transmitter_id: int,
    expected_experiment_id: int,
) -> VendorInfo:
    magic8 = expected_magic[:8].ljust(8, b"\x00")
    for eid, payload in ies:
        if eid != 221 or len(payload) < 21:
            continue
        if payload[:3] != expected_oui or payload[3] != expected_type:
            continue
        magic = payload[4:12]
        version = payload[12]
        tx_id = int.from_bytes(payload[13:15], "big")
        exp_id = int.from_bytes(payload[15:17], "big")
        counter = int.from_bytes(payload[17:21], "big")

        reasons = []
        if magic != magic8:
            reasons.append("magic mismatch")
        if version != expected_version:
            reasons.append("version mismatch")
        if tx_id != expected_transmitter_id:
            reasons.append("transmitter id mismatch")
        if exp_id != expected_experiment_id:
            reasons.append("experiment id mismatch")
        return VendorInfo(
            valid=not reasons,
            packet_counter=counter,
            transmitter_id=tx_id,
            experiment_id=exp_id,
            reason="OK" if not reasons else "; ".join(reasons),
        )

    return VendorInfo(False, None, None, None, "Vendor IE not found")


def parse_beacon(
    mpdu: bytes,
    *,
    expected_ssid: str,
    expected_bssid: str,
    expected_oui: bytes,
    expected_vendor_type: int,
    expected_magic: bytes,
    expected_version: int,
    expected_transmitter_id: int,
    expected_experiment_id: int,
) -> BeaconInfo:
    empty_vendor = VendorInfo(False, None, None, None, "not parsed")
    if len(mpdu) < 40:
        return BeaconInfo(False, False, False, None, None, None, None, None, empty_vendor, "MPDU too short")

    calc = struct.pack("<I", zlib.crc32(mpdu[:-4]) & 0xFFFFFFFF)
    fcs_valid = mpdu[-4:] == calc

    fc = int.from_bytes(mpdu[0:2], "little")
    frame_type = (fc >> 2) & 0x3
    subtype = (fc >> 4) & 0xF
    is_beacon = frame_type == 0 and subtype == 8

    source = mac_string(mpdu[10:16])
    bssid = mac_string(mpdu[16:22])
    seq_ctrl = int.from_bytes(mpdu[22:24], "little")
    seq = (seq_ctrl >> 4) & 0xFFF

    body = mpdu[24:-4]
    interval = int.from_bytes(body[8:10], "little") if len(body) >= 10 else None
    ies = parse_ies(body)

    ssid = None
    for eid, payload in ies:
        if eid == 0:
            ssid = payload.decode("utf-8", errors="replace")
            break

    vendor = parse_vendor(
        ies,
        expected_oui=expected_oui,
        expected_type=expected_vendor_type,
        expected_magic=expected_magic,
        expected_version=expected_version,
        expected_transmitter_id=expected_transmitter_id,
        expected_experiment_id=expected_experiment_id,
    )

    reasons = []
    if not fcs_valid:
        reasons.append("FCS failed")
    if not is_beacon:
        reasons.append("not a beacon")
    if ssid != expected_ssid:
        reasons.append("SSID mismatch")
    if source.lower() != expected_bssid.lower():
        reasons.append("source mismatch")
    if bssid.lower() != expected_bssid.lower():
        reasons.append("BSSID mismatch")
    if not vendor.valid:
        reasons.append(vendor.reason)

    return BeaconInfo(
        valid=not reasons,
        fcs_valid=fcs_valid,
        is_beacon=is_beacon,
        ssid=ssid,
        source=source,
        bssid=bssid,
        sequence_number=seq,
        beacon_interval_tu=interval,
        vendor=vendor,
        reason="OK" if not reasons else "; ".join(reasons),
    )
