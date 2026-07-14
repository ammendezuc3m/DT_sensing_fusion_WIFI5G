from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np

from .common import SAMPLE_RATE, correct_cfo
from .detection import StfCandidate, detect_stf
from .mac import BeaconInfo, parse_beacon
from .phy import ChannelEstimate, SignalInfo, data_symbol_count, decode_lsig, decode_psdu, estimate_channel
from .sync import SyncResult, synchronize


@dataclass
class ReceiverConfig:
    sample_rate: float = SAMPLE_RATE
    stf_threshold: float = 0.65
    stf_min_plateau: int = 48
    min_separation_samples: int = 4800
    min_ltf_template_metric: float = 0.08
    max_ltf_consistency_error: float = 0.35
    expected_ssid: str = "SENSING_WIFI"
    expected_bssid: str = "02:11:22:33:44:55"
    expected_oui: bytes = b"\x02\x11\x22"
    expected_vendor_type: int = 1
    expected_magic: bytes = b"ALBSENS"
    expected_version: int = 1
    expected_transmitter_id: int = 1
    expected_experiment_id: int = 1


@dataclass
class DecodedBeacon:
    offset: int
    stf_metric: float
    ltf_template_metric: float
    ltf_repeat_metric: float
    coarse_cfo_hz: float
    fine_cfo_hz: float
    total_cfo_hz: float
    ltf_consistency_error: float
    signal: SignalInfo
    beacon: BeaconInfo
    csi: np.ndarray

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("csi", None)
        out["csi_abs_mean"] = float(np.mean(np.abs(self.csi)))
        out["csi_abs_std"] = float(np.std(np.abs(self.csi)))
        return out


@dataclass
class Reject:
    coarse_offset: int
    stf_metric: float
    reason: str


def decode_candidate(
    iq: np.ndarray,
    candidate: StfCandidate,
    cfg: ReceiverConfig,
) -> DecodedBeacon:
    sync = synchronize(
        iq,
        candidate,
        sample_rate=cfg.sample_rate,
        min_template_metric=cfg.min_ltf_template_metric,
    )

    available = np.asarray(iq[sync.packet_offset:], dtype=np.complex64)
    if len(available) < 400:
        raise ValueError("Truncated candidate")

    corrected = correct_cfo(
        available,
        sync.total_cfo_hz,
        sample_rate=cfg.sample_rate,
        start_index=sync.packet_offset,
    )

    chan = estimate_channel(corrected)
    if chan.consistency_error > cfg.max_ltf_consistency_error:
        raise ValueError(
            f"L-LTF consistency error {chan.consistency_error:.4f} "
            f"> {cfg.max_ltf_consistency_error:.4f}"
        )

    sig = decode_lsig(corrected, chan.csi)
    if not sig.valid:
        raise ValueError(f"L-SIG invalid: {sig.reason}")

    n_sym = data_symbol_count(sig.length_bytes)
    packet_samples = 400 + n_sym * 80
    if len(corrected) < packet_samples:
        raise ValueError("Truncated PPDU")

    psdu = decode_psdu(
        corrected[:packet_samples],
        chan.csi,
        sig.length_bytes,
        scrambler_state=0x5D,
    )

    beacon = parse_beacon(
        psdu,
        expected_ssid=cfg.expected_ssid,
        expected_bssid=cfg.expected_bssid,
        expected_oui=cfg.expected_oui,
        expected_vendor_type=cfg.expected_vendor_type,
        expected_magic=cfg.expected_magic,
        expected_version=cfg.expected_version,
        expected_transmitter_id=cfg.expected_transmitter_id,
        expected_experiment_id=cfg.expected_experiment_id,
    )
    if not beacon.valid:
        raise ValueError(f"Beacon identity failed: {beacon.reason}")

    return DecodedBeacon(
        offset=sync.packet_offset,
        stf_metric=candidate.metric,
        ltf_template_metric=sync.ltf_template_metric,
        ltf_repeat_metric=sync.ltf_repeat_metric,
        coarse_cfo_hz=sync.coarse_cfo_hz,
        fine_cfo_hz=sync.fine_cfo_hz,
        total_cfo_hz=sync.total_cfo_hz,
        ltf_consistency_error=chan.consistency_error,
        signal=sig,
        beacon=beacon,
        csi=chan.csi,
    )


def decode_capture(
    iq: np.ndarray,
    cfg: ReceiverConfig,
) -> tuple[list[DecodedBeacon], list[Reject]]:
    candidates = detect_stf(
        iq,
        sample_rate=cfg.sample_rate,
        threshold=cfg.stf_threshold,
        min_plateau=cfg.stf_min_plateau,
        min_separation=cfg.min_separation_samples,
    )

    accepted: list[DecodedBeacon] = []
    rejected: list[Reject] = []

    for candidate in candidates:
        try:
            accepted.append(decode_candidate(iq, candidate, cfg))
        except Exception as exc:
            rejected.append(
                Reject(
                    coarse_offset=candidate.coarse_offset,
                    stf_metric=candidate.metric,
                    reason=str(exc),
                )
            )
    return accepted, rejected
