#!/usr/bin/env python3
"""
Legacy 802.11a/g OFDM PHY waveform generator for 20 MHz, 6 Mb/s.

It converts a PSDU/MPDU into a non-HT OFDM PPDU:

  L-STF + L-LTF + L-SIG + DATA

Rate:
  6 Mb/s = BPSK, coding rate 1/2.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

try:
    from .wifi_beacon_mac import BeaconConfig, build_beacon_mpdu, bytes_to_bits_lsb_first
except ImportError:
    from wifi_beacon_mac import BeaconConfig, build_beacon_mpdu, bytes_to_bits_lsb_first


FFT_SIZE = 64
CP_LEN = 16
SAMPLE_RATE = 20_000_000.0

DATA_SUBCARRIERS = np.array(
    list(range(-26, -21)) +
    list(range(-20, -7)) +
    list(range(-6, 0)) +
    list(range(1, 7)) +
    list(range(8, 21)) +
    list(range(22, 27)),
    dtype=int,
)

PILOT_SUBCARRIERS = np.array([-21, -7, 7, 21], dtype=int)
ACTIVE_SUBCARRIERS = np.array([k for k in range(-26, 27) if k != 0], dtype=int)


def sc_to_bin(sc: int) -> int:
    return sc % FFT_SIZE


def bits_from_int_lsb(value: int, nbits: int) -> list[int]:
    return [(value >> i) & 1 for i in range(nbits)]


def ofdm_ifft(freq_bins: np.ndarray) -> np.ndarray:
    if len(freq_bins) != FFT_SIZE:
        raise ValueError("freq_bins must have length 64")
    return np.fft.ifft(freq_bins, n=FFT_SIZE) * FFT_SIZE / np.sqrt(52)


def ofdm_symbol_from_carriers(carriers: dict[int, complex], add_cp: bool = True) -> np.ndarray:
    freq = np.zeros(FFT_SIZE, dtype=np.complex64)
    for sc, value in carriers.items():
        if sc == 0 or sc < -32 or sc > 31:
            raise ValueError(f"Invalid subcarrier: {sc}")
        freq[sc_to_bin(sc)] = value

    td = ofdm_ifft(freq)

    if add_cp:
        td = np.concatenate([td[-CP_LEN:], td])

    return td.astype(np.complex64)


def ltf_freq_sequence() -> dict[int, complex]:
    vals = [
        1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1,
        1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1,
        0,
        1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1,
        -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1,
    ]

    if len(vals) != 53:
        raise RuntimeError("Bad LTF sequence length")

    return {sc: complex(v, 0) for sc, v in zip(range(-26, 27), vals) if sc != 0}


def stf_freq_sequence() -> dict[int, complex]:
    vals_53 = [
        0, 0, 1 + 1j, 0, 0, 0, -1 - 1j, 0, 0, 0, 1 + 1j, 0, 0,
        0, -1 - 1j, 0, 0, 0, -1 - 1j, 0, 0, 0, 1 + 1j, 0, 0, 0,
        0,
        0, 0, 0, -1 - 1j, 0, 0, 0, -1 - 1j, 0, 0, 0, 1 + 1j, 0,
        0, 0, 1 + 1j, 0, 0, 0, 1 + 1j, 0, 0, 0, 1 + 1j, 0, 0,
    ]

    if len(vals_53) != 53:
        raise RuntimeError("Bad STF sequence length")

    scale = np.sqrt(13 / 6)

    return {
        sc: scale * complex(v)
        for sc, v in zip(range(-26, 27), vals_53)
        if sc != 0 and v != 0
    }


def generate_l_stf() -> np.ndarray:
    freq = np.zeros(FFT_SIZE, dtype=np.complex64)

    for sc, v in stf_freq_sequence().items():
        freq[sc_to_bin(sc)] = v

    short64 = ofdm_ifft(freq)
    short16 = short64[:16]

    return np.tile(short16, 10).astype(np.complex64)


def generate_l_ltf() -> np.ndarray:
    freq = np.zeros(FFT_SIZE, dtype=np.complex64)

    for sc, v in ltf_freq_sequence().items():
        freq[sc_to_bin(sc)] = v

    long64 = ofdm_ifft(freq)

    return np.concatenate([long64[-32:], long64, long64]).astype(np.complex64)


def pilot_values(symbol_index: int) -> dict[int, complex]:
    # First implementation: fixed legacy pilot values.
    # If Wireshark decoding is unstable, the first thing to refine is pilot polarity.
    return {-21: 1 + 0j, -7: 1 + 0j, 7: 1 + 0j, 21: -1 + 0j}


def convolutional_encode(bits: list[int]) -> list[int]:
    g0 = 0o133
    g1 = 0o171
    state = 0
    out: list[int] = []

    for bit in bits:
        state = ((state << 1) | (int(bit) & 1)) & 0x7F

        for g in (g0, g1):
            out.append((state & g).bit_count() & 1)

    return out


def interleave_48(bits: list[int]) -> list[int]:
    if len(bits) != 48:
        raise ValueError("6 Mb/s OFDM interleaver expects 48 coded bits")

    n_cbps = 48
    out = [0] * n_cbps

    for k in range(n_cbps):
        i = (n_cbps // 16) * (k % 16) + (k // 16)
        out[i] = bits[k]

    return out


def bpsk_map(bits: list[int]) -> np.ndarray:
    return np.array([1.0 if int(b) == 0 else -1.0 for b in bits], dtype=np.complex64)


def make_signal_bits(psdu_length_bytes: int) -> list[int]:
    # L-SIG:
    # RATE(4) + reserved(1) + LENGTH(12) + parity(1) + tail(6)
    # RATE bits for 6 Mb/s legacy OFDM: 1101.
    rate_bits = [1, 1, 0, 1]
    reserved = [0]
    length_bits = bits_from_int_lsb(psdu_length_bytes, 12)

    first17 = rate_bits + reserved + length_bits
    parity = [sum(first17) & 1]
    tail = [0] * 6

    bits = first17 + parity + tail

    if len(bits) != 24:
        raise RuntimeError("SIGNAL field must have 24 bits")

    return bits


def make_ofdm_symbol_from_48_data_bits(data_bits: list[int], symbol_index: int) -> np.ndarray:
    if len(data_bits) != 48:
        raise ValueError("Expected 48 BPSK bits for one OFDM symbol")

    mapped = bpsk_map(data_bits)
    carriers: dict[int, complex] = {}

    for sc, val in zip(DATA_SUBCARRIERS, mapped):
        carriers[int(sc)] = complex(val)

    carriers.update(pilot_values(symbol_index))

    return ofdm_symbol_from_carriers(carriers, add_cp=True)


def encode_signal_symbol(psdu_length_bytes: int) -> np.ndarray:
    bits24 = make_signal_bits(psdu_length_bytes)
    coded48 = convolutional_encode(bits24)
    interleaved = interleave_48(coded48)

    return make_ofdm_symbol_from_48_data_bits(interleaved, symbol_index=0)


def scramble_bits(bits: list[int], initial_state: int = 0x5D) -> list[int]:
    if not 1 <= initial_state <= 0x7F:
        raise ValueError("initial_state must be a non-zero 7-bit value")

    state = [(initial_state >> i) & 1 for i in range(7)]
    out: list[int] = []

    for bit in bits:
        feedback = state[6] ^ state[3]
        out_bit = int(bit) ^ feedback
        out.append(out_bit)
        state = [feedback] + state[:6]

    return out


def encode_data_symbols(psdu: bytes, scrambler_state: int = 0x5D) -> np.ndarray:
    n_dbps = 24

    service = [0] * 16
    psdu_bits = bytes_to_bits_lsb_first(psdu)
    tail = [0] * 6

    data_bits = service + psdu_bits + tail

    n_sym = int(np.ceil(len(data_bits) / n_dbps))
    n_data = n_sym * n_dbps

    pad = [0] * (n_data - len(data_bits))
    data_bits = data_bits + pad

    scrambled = scramble_bits(data_bits, initial_state=scrambler_state)

    tail_start = 16 + len(psdu_bits)
    scrambled[tail_start:tail_start + 6] = [0] * 6

    coded = convolutional_encode(scrambled)

    if len(coded) != n_sym * 48:
        raise RuntimeError("Unexpected coded DATA length")

    symbols = []

    for s in range(n_sym):
        block = coded[s * 48:(s + 1) * 48]
        interleaved = interleave_48(block)
        symbols.append(make_ofdm_symbol_from_48_data_bits(interleaved, symbol_index=s + 1))

    return np.concatenate(symbols).astype(np.complex64)


def make_nonht_6mbps_ppdu(psdu: bytes, scrambler_state: int = 0x5D) -> np.ndarray:
    ppdu = np.concatenate([
        generate_l_stf(),
        generate_l_ltf(),
        encode_signal_symbol(len(psdu)),
        encode_data_symbols(psdu, scrambler_state=scrambler_state),
    ]).astype(np.complex64)

    peak = np.max(np.abs(ppdu))

    if peak > 0:
        ppdu = (0.55 * ppdu / peak).astype(np.complex64)

    return ppdu


def make_beacon_waveform(
    ssid: str = "SENSING_WIFI",
    bssid: str = "02:11:22:33:44:55",
    channel: int = 1,
    beacon_interval_tu: int = 98,
    sequence_number: int = 0,
    timestamp_us: int = 0,
    profile: str = "router_like_wpa2",
    scrambler_state: int = 0x5D,
) -> tuple[np.ndarray, bytes]:

    cfg = BeaconConfig(
        ssid=ssid,
        bssid=bssid,
        beacon_interval_tu=beacon_interval_tu,
        channel=channel,
        sequence_number=sequence_number,
        timestamp_us=timestamp_us,
        profile=profile,
    )

    mpdu = build_beacon_mpdu(cfg, include_fcs=True)
    waveform = make_nonht_6mbps_ppdu(mpdu, scrambler_state=scrambler_state)

    return waveform, mpdu


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ssid", default="SENSING_WIFI")
    p.add_argument("--bssid", default="02:11:22:33:44:55")
    p.add_argument("--channel", type=int, default=1)
    p.add_argument("--beacon-interval-tu", type=int, default=98)
    p.add_argument("--sequence-number", type=int, default=0)
    p.add_argument("--timestamp-us", type=int, default=0)
    p.add_argument("--profile", choices=["minimal_open", "router_like_wpa2"], default="router_like_wpa2")
    p.add_argument("--output-npz", default="results/wifi_debug/beacon_ofdm_6mbps.npz")
    args = p.parse_args()

    wf, mpdu = make_beacon_waveform(
        ssid=args.ssid,
        bssid=args.bssid,
        channel=args.channel,
        beacon_interval_tu=args.beacon_interval_tu,
        sequence_number=args.sequence_number,
        timestamp_us=args.timestamp_us,
        profile=args.profile,
    )

    out = Path(args.output_npz)
    out.parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        out,
        waveform=wf,
        mpdu=np.frombuffer(mpdu, dtype=np.uint8),
        sample_rate_hz=np.array([SAMPLE_RATE]),
    )

    print(f"Wrote {out}")
    print(f"Waveform samples: {len(wf)}")
    print(f"Duration us: {len(wf) / SAMPLE_RATE * 1e6:.3f}")
    print(f"MPDU bytes: {len(mpdu)}")


if __name__ == "__main__":
    main()
