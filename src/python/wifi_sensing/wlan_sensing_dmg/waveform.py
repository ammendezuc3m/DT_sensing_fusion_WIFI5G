#!/usr/bin/env python3
"""
DMG/802.11bf-inspired sensing PPDU waveform generator.

This is a waveform-only experimental implementation for USRP sensing tests.

It is inspired by:
    - 802.11ad/ay DMG/EDMG PPDU structure
    - 802.11bf WLAN sensing use cases
    - Golay-based STF/CEF/channel-estimation/training fields
    - TRN-like training subfields used for beam/sensing measurements

It does not implement:
    - complete IEEE 802.11bf MAC negotiation
    - commercial WiGig/DMG/EDMG interoperability
    - real 60 GHz PHY bandwidth/rate
    - exact standardized 802.11ad/ay/bf sequence tables

Current scaled test profile:
    RF:       sub-6 GHz with USRP B210
    Fs:       20 Msps
    Signal:   single-carrier baseband
    Fields:   STF + CEF + header-like + known sensing payload + TRN-like field
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import json
import numpy as np

try:
    from .golay import (
        bpsk_to_complex,
        complementary_sidelobe_check,
        golay_pair,
        make_sign_code,
        repeat_to_length,
    )
except ImportError:
    from golay import (
        bpsk_to_complex,
        complementary_sidelobe_check,
        golay_pair,
        make_sign_code,
        repeat_to_length,
    )


@dataclass(frozen=True)
class DmgSensingWaveformConfig:
    """
    DMG-like sensing PPDU profile scaled to 20 Msps.
    """

    sample_rate_hz: float = 20e6

    # Amplitude normalization.
    peak: float = 0.55

    # Guard/taper for clean USRP bursts.
    guard_len: int = 256
    taper_len: int = 64

    # STF: repeated short Golay sequence.
    stf_golay_len: int = 128
    stf_repetitions: int = 16

    # CEF: complementary pair used for channel estimation.
    cef_golay_len: int = 512
    cef_repetitions: int = 1

    # Header-like known field. This is not a real MAC header.
    header_len: int = 512

    # Known sensing payload. Longer = more observations/energy for sensing.
    sensing_block_len: int = 512
    sensing_blocks: int = 16

    # TRN-like field: training units and subfields.
    trn_golay_len: int = 128
    trn_units: int = 4
    trn_subfields_per_unit: int = 4

    # Deterministic known content.
    seed: int = 80211

    schema_version: str = "dmg_like_sensing_ppdu_v1"


def cosine_taper_edges(x: np.ndarray, taper_len: int) -> np.ndarray:
    """
    Apply smooth fade-in/fade-out to the complete burst.

    This reduces hard edges in the USRP burst.
    """
    y = np.asarray(x, dtype=np.complex64).copy()

    if taper_len <= 0:
        return y

    n = len(y)
    if n < 2 * taper_len:
        raise ValueError("Waveform too short for requested taper_len")

    t = np.arange(taper_len, dtype=np.float32)
    ramp = 0.5 * (1.0 - np.cos(np.pi * (t + 1) / taper_len))

    y[:taper_len] *= ramp
    y[-taper_len:] *= ramp[::-1]

    return y.astype(np.complex64)


def make_header_like_bits(cfg: DmgSensingWaveformConfig) -> np.ndarray:
    """
    Create a known deterministic header-like BPSK field.

    This is not a real IEEE MAC/PHY header. It is a compact known field so that
    the waveform has a stable identifiable version/profile block.

    Encoded words:
        magic
        version
        sensing_blocks
        trn_units
        trn_subfields_per_unit
        seed
    """
    words = [
        0xD06D_BF01,                    # magic marker: DMG/BF-like v1
        0x0000_0001,                    # version
        int(cfg.sensing_blocks) & 0xFFFF,
        int(cfg.trn_units) & 0xFFFF,
        int(cfg.trn_subfields_per_unit) & 0xFFFF,
        int(cfg.seed) & 0xFFFF_FFFF,
    ]

    bits = []
    for w in words:
        for i in range(31, -1, -1):
            bits.append((w >> i) & 1)

    bits = np.asarray(bits, dtype=np.int8)

    bpsk = 2.0 * bits.astype(np.float32) - 1.0
    bpsk = repeat_to_length(bpsk, cfg.header_len).astype(np.float32)

    return bpsk


def make_known_sensing_blocks(cfg: DmgSensingWaveformConfig) -> np.ndarray:
    """
    Build deterministic known sensing blocks.

    Each block is BPSK and known by the receiver.

    Shape:
        [sensing_blocks, sensing_block_len]
    """
    ga512, gb512 = golay_pair(cfg.cef_golay_len)

    base = np.concatenate([ga512, gb512]).astype(np.float32)

    blocks = []

    for i in range(cfg.sensing_blocks):
        sign_code = make_sign_code(len(base), seed=cfg.seed + 1000 + i)
        rolled = np.roll(base, shift=(i * 37) % len(base))
        seq = rolled * sign_code
        seq = repeat_to_length(seq, cfg.sensing_block_len)
        blocks.append(seq.astype(np.float32))

    return np.stack(blocks, axis=0).astype(np.float32)


def make_trn_like_field(cfg: DmgSensingWaveformConfig) -> np.ndarray:
    """
    Build TRN-like units/subfields.

    Structure:
        TRN field:
            TRN-Unit 0:
                subfield 0
                subfield 1
                ...
            TRN-Unit 1:
                subfield 0
                ...

    Shape before flatten:
        [trn_units, trn_subfields_per_unit, trn_golay_len]

    The sign/roll pattern gives every subfield a known but distinct training
    sequence. In a real EDMG/802.11az/bf system, these subfields may be linked
    to antenna weight vectors, beam/polarization states, or LOS assessment.
    """
    ga, gb = golay_pair(cfg.trn_golay_len)

    units = []

    for u in range(cfg.trn_units):
        subfields = []

        for p in range(cfg.trn_subfields_per_unit):
            base = ga if ((u + p) % 2 == 0) else gb

            # Deterministic sign code per unit/subfield.
            sign = make_sign_code(cfg.trn_golay_len, seed=cfg.seed + 2000 + 100 * u + p)

            # Small deterministic cyclic shift.
            seq = np.roll(base, shift=(13 * u + 7 * p) % cfg.trn_golay_len) * sign

            subfields.append(seq.astype(np.float32))

        units.append(np.stack(subfields, axis=0))

    return np.stack(units, axis=0).astype(np.float32)


def append_field(
    parts: list[np.ndarray],
    field_map: dict[str, dict[str, int]],
    name: str,
    x: np.ndarray,
) -> None:
    start = sum(len(p) for p in parts)
    parts.append(np.asarray(x, dtype=np.complex64))
    stop = sum(len(p) for p in parts)

    field_map[name] = {
        "start": int(start),
        "stop": int(stop),
        "num_samples": int(stop - start),
    }


def build_dmg_sensing_waveform(cfg: DmgSensingWaveformConfig) -> dict[str, Any]:
    """
    Build complete DMG-like sensing PPDU.
    """

    # Core Golay sequences.
    ga_stf, gb_stf = golay_pair(cfg.stf_golay_len)
    ga_cef, gb_cef = golay_pair(cfg.cef_golay_len)

    # STF: repeated short Golay sequence.
    stf = np.tile(ga_stf, cfg.stf_repetitions).astype(np.float32)

    # CEF: complementary long sequences. Repeat pair if requested.
    cef_pair = np.concatenate([ga_cef, gb_cef]).astype(np.float32)
    cef = np.tile(cef_pair, cfg.cef_repetitions).astype(np.float32)

    # Header-like field.
    header = make_header_like_bits(cfg)

    # Known data/sensing payload.
    sensing_blocks = make_known_sensing_blocks(cfg)
    sensing_payload = sensing_blocks.reshape(-1).astype(np.float32)

    # TRN-like field.
    trn_units = make_trn_like_field(cfg)
    trn = trn_units.reshape(-1).astype(np.float32)

    guard = np.zeros(cfg.guard_len, dtype=np.complex64)

    parts: list[np.ndarray] = []
    field_map: dict[str, dict[str, int]] = {}

    append_field(parts, field_map, "guard_pre", guard)
    append_field(parts, field_map, "stf", bpsk_to_complex(stf))
    append_field(parts, field_map, "cef", bpsk_to_complex(cef))
    append_field(parts, field_map, "header_like", bpsk_to_complex(header))
    append_field(parts, field_map, "known_sensing_payload", bpsk_to_complex(sensing_payload))
    append_field(parts, field_map, "trn_like", bpsk_to_complex(trn))
    append_field(parts, field_map, "guard_post", guard)

    waveform = np.concatenate(parts).astype(np.complex64)

    # Normalize.
    peak_before = float(np.max(np.abs(waveform)) + 1e-12)

    if cfg.peak > 0:
        waveform = (waveform / peak_before * cfg.peak).astype(np.complex64)

    waveform = cosine_taper_edges(waveform, cfg.taper_len)

    duration_us = len(waveform) / cfg.sample_rate_hz * 1e6

    stf_check = complementary_sidelobe_check(ga_stf, gb_stf)
    cef_check = complementary_sidelobe_check(ga_cef, gb_cef)

    metadata = {
        "schema_version": cfg.schema_version,
        "description": (
            "DMG/802.11bf-inspired sensing PPDU scaled to 20 Msps for USRP tests. "
            "Waveform-only implementation: STF + CEF + known header-like field + "
            "known sensing payload + TRN-like training field."
        ),
        "compliance_note": (
            "This is not a complete IEEE 802.11bf/802.11ad/802.11ay compliant PPDU. "
            "It is a DMG/EDMG-inspired PHY waveform for sensing experiments without "
            "MAC negotiation, association or commercial interoperability."
        ),
        "rf_profile": "sub6_scaled_dmg_like_test_profile",
        "intended_use": "active_sensing_usrp_experiment",
        "sample_rate_hz": float(cfg.sample_rate_hz),
        "waveform_samples": int(len(waveform)),
        "duration_us": float(duration_us),
        "peak": float(np.max(np.abs(waveform))),
        "field_map": field_map,
        "stf": {
            "golay_len": int(cfg.stf_golay_len),
            "repetitions": int(cfg.stf_repetitions),
            "samples": int(len(stf)),
            "duration_us": float(len(stf) / cfg.sample_rate_hz * 1e6),
            "complementary_check": stf_check,
        },
        "cef": {
            "golay_len": int(cfg.cef_golay_len),
            "repetitions": int(cfg.cef_repetitions),
            "samples": int(len(cef)),
            "duration_us": float(len(cef) / cfg.sample_rate_hz * 1e6),
            "complementary_check": cef_check,
        },
        "header_like": {
            "samples": int(len(header)),
            "duration_us": float(len(header) / cfg.sample_rate_hz * 1e6),
            "real_mac_header": False,
        },
        "known_sensing_payload": {
            "num_blocks": int(cfg.sensing_blocks),
            "block_len": int(cfg.sensing_block_len),
            "samples": int(len(sensing_payload)),
            "duration_us": float(len(sensing_payload) / cfg.sample_rate_hz * 1e6),
        },
        "trn_like": {
            "trn_units": int(cfg.trn_units),
            "trn_subfields_per_unit": int(cfg.trn_subfields_per_unit),
            "trn_golay_len": int(cfg.trn_golay_len),
            "samples": int(len(trn)),
            "duration_us": float(len(trn) / cfg.sample_rate_hz * 1e6),
        },
        "config": asdict(cfg),
    }

    return {
        "waveform": waveform.astype(np.complex64),
        "stf_sequence": stf.astype(np.float32),
        "cef_ga": ga_cef.astype(np.float32),
        "cef_gb": gb_cef.astype(np.float32),
        "header_like": header.astype(np.float32),
        "known_sensing_blocks": sensing_blocks.astype(np.float32),
        "trn_units": trn_units.astype(np.float32),
        "metadata": metadata,
    }


def save_dmg_sensing_waveform_npz(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        p,
        waveform=payload["waveform"],
        stf_sequence=payload["stf_sequence"],
        cef_ga=payload["cef_ga"],
        cef_gb=payload["cef_gb"],
        header_like=payload["header_like"],
        known_sensing_blocks=payload["known_sensing_blocks"],
        trn_units=payload["trn_units"],
        metadata_json=json.dumps(payload["metadata"], indent=2),
    )


def load_dmg_sensing_waveform_npz(path: str | Path) -> dict[str, Any]:
    data = np.load(path, allow_pickle=False)

    return {
        "waveform": data["waveform"].astype(np.complex64),
        "stf_sequence": data["stf_sequence"].astype(np.float32),
        "cef_ga": data["cef_ga"].astype(np.float32),
        "cef_gb": data["cef_gb"].astype(np.float32),
        "header_like": data["header_like"].astype(np.float32),
        "known_sensing_blocks": data["known_sensing_blocks"].astype(np.float32),
        "trn_units": data["trn_units"].astype(np.float32),
        "metadata": json.loads(str(data["metadata_json"])),
    }


def main() -> None:
    import argparse

    p = argparse.ArgumentParser()

    p.add_argument("--output-npz", default="results/wlan_sensing_dmg/dmg_like_sensing_ppdu_v1.npz")
    p.add_argument("--sample-rate", type=float, default=20e6)
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

    args = p.parse_args()

    cfg = DmgSensingWaveformConfig(
        sample_rate_hz=args.sample_rate,
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

    meta = payload["metadata"]

    print("DMG-like sensing PPDU waveform generated")
    print(f"  output: {args.output_npz}")
    print(f"  samples: {meta['waveform_samples']}")
    print(f"  duration_us: {meta['duration_us']:.3f}")
    print(f"  peak: {meta['peak']:.4f}")
    print("  fields:")
    for name, fm in meta["field_map"].items():
        print(f"    {name}: {fm['num_samples']} samples")


if __name__ == "__main__":
    main()
