#!/usr/bin/env python3
"""
Transmite beacons Wi‑Fi legacy mínimos con una USRP B210.

Objetivo:
    Que una tarjeta Wi‑Fi convencional pueda descubrir el SSID mediante
    `nmcli device wifi list`.

El script reutiliza el generador Non-HT del repositorio y transmite un
superframe periódico:
    [beacon válido][ceros] ... cada 100 TU (102,4 ms)

Uso recomendado desde la raíz del repositorio:
    source .venv_uhd/bin/activate
    python -m tests.wifi.python.tx_visible_wifi_beacon \
        --serial 34B739F \
        --channel-index 0 \
        --wifi-channel 13 \
        --ssid USRP_VISIBLE_TEST \
        --gain 10

Después, en el PC con tarjeta Wi‑Fi:
    nmcli device wifi rescan ifname wlp4s0
    nmcli -f SSID,BSSID,CHAN,FREQ,SIGNAL device wifi list ifname wlp4s0
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from pathlib import Path

import numpy as np
import uhd

from src.python.wifi_sensing.tx_wifi_usrp import build_vendor_specific_ie
from src.python.wifi_sensing.wifi_legacy_ofdm import make_beacon_waveform


TU_SECONDS = 1024e-6
SAMPLE_RATE = 20e6
BEACON_INTERVAL_TU = 100
PERIOD_SAMPLES = int(round(BEACON_INTERVAL_TU * TU_SECONDS * SAMPLE_RATE))
PERIOD_SECONDS = PERIOD_SAMPLES / SAMPLE_RATE

# Canales de 2,4 GHz usados en Europa.
CHANNEL_TO_FREQ_HZ = {
    channel: (2407 + 5 * channel) * 1e6
    for channel in range(1, 14)
}

RUNNING = True


def stop_handler(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transmite un SSID visible mediante beacons Wi-Fi legacy."
    )
    parser.add_argument("--serial", default="34B739F")
    parser.add_argument(
        "--channel-index",
        type=int,
        choices=(0, 1),
        default=0,
        help="Canal lógico TX de la B210. Mueve la antena al TX/RX correspondiente.",
    )
    parser.add_argument(
        "--wifi-channel",
        type=int,
        choices=range(1, 14),
        default=13,
        metavar="1..13",
    )
    parser.add_argument("--ssid", default="USRP_VISIBLE_TEST")
    parser.add_argument("--bssid", default="02:11:22:33:44:55")
    parser.add_argument("--gain", type=float, default=10.0)
    parser.add_argument("--amplitude", type=float, default=0.35)
    parser.add_argument(
        "--periods-per-superframe",
        type=int,
        default=4,
        help="Número de periodos de 102,4 ms precalculados. Predeterminado: 4.",
    )
    parser.add_argument(
        "--save-npz",
        default="results/wifi_debug/visible_wifi_beacon.npz",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= len(args.ssid.encode("utf-8")) <= 32:
        raise SystemExit("El SSID debe ocupar entre 1 y 32 bytes UTF-8.")
    if not 0.0 < args.amplitude <= 0.8:
        raise SystemExit("--amplitude debe estar en (0, 0.8].")
    if args.periods_per_superframe < 1:
        raise SystemExit("--periods-per-superframe debe ser >= 1.")


def make_vendor_ie(counter: int) -> bytes:
    return build_vendor_specific_ie(
        oui=bytes.fromhex("021122"),
        vendor_type=1,
        magic=b"ALBSENS",
        version=1,
        transmitter_id=1,
        experiment_id=1,
        packet_counter=counter,
    )


def build_superframe(args: argparse.Namespace) -> tuple[np.ndarray, bytes]:
    total_samples = PERIOD_SAMPLES * args.periods_per_superframe
    superframe = np.zeros(total_samples, dtype=np.complex64)
    first_mpdu = b""

    for index in range(args.periods_per_superframe):
        vendor_ie = make_vendor_ie(index)

        waveform, mpdu = make_beacon_waveform(
            ssid=args.ssid,
            bssid=args.bssid,
            channel=args.wifi_channel,
            beacon_interval_tu=BEACON_INTERVAL_TU,
            sequence_number=index & 0xFFF,
            timestamp_us=int(round(index * PERIOD_SECONDS * 1e6)),
            profile="router_like_wpa2",
            extra_ies=[vendor_ie],
        )

        waveform = np.asarray(waveform, dtype=np.complex64).reshape(-1)
        peak = float(np.max(np.abs(waveform)))

        if peak <= 0:
            raise RuntimeError("La waveform generada tiene amplitud cero.")
        if waveform.size > PERIOD_SAMPLES:
            raise RuntimeError(
                f"Beacon demasiado largo: {waveform.size} > {PERIOD_SAMPLES}."
            )

        waveform *= np.float32(args.amplitude / peak)

        start = index * PERIOD_SAMPLES
        superframe[start : start + waveform.size] = waveform

        if index == 0:
            first_mpdu = bytes(mpdu)

    return superframe, first_mpdu


def save_reference(
    path_text: str,
    superframe: np.ndarray,
    mpdu: bytes,
    args: argparse.Namespace,
) -> None:
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "ssid": args.ssid,
        "bssid": args.bssid,
        "wifi_channel": args.wifi_channel,
        "center_frequency_hz": CHANNEL_TO_FREQ_HZ[args.wifi_channel],
        "sample_rate_hz": SAMPLE_RATE,
        "beacon_interval_tu": BEACON_INTERVAL_TU,
        "period_samples": PERIOD_SAMPLES,
        "periods_per_superframe": args.periods_per_superframe,
        "channel_index": args.channel_index,
        "gain_db": args.gain,
        "amplitude": args.amplitude,
    }

    np.savez(
        path,
        waveform=superframe,
        mpdu=np.frombuffer(mpdu, dtype=np.uint8),
        sample_rate_hz=np.array([SAMPLE_RATE], dtype=np.float64),
        metadata_json=np.array([json.dumps(metadata)]),
    )


def configure_usrp(args: argparse.Namespace):
    center_frequency = CHANNEL_TO_FREQ_HZ[args.wifi_channel]

    usrp = uhd.usrp.MultiUSRP(f"serial={args.serial}")
    usrp.set_tx_rate(SAMPLE_RATE, args.channel_index)
    usrp.set_tx_freq(
        uhd.types.TuneRequest(center_frequency),
        args.channel_index,
    )
    usrp.set_tx_gain(args.gain, args.channel_index)
    usrp.set_tx_antenna("TX/RX", args.channel_index)

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [args.channel_index]
    streamer = usrp.get_tx_stream(stream_args)

    return usrp, streamer


def send_all(streamer, samples: np.ndarray, metadata) -> tuple[int, int]:
    offset = 0
    zero_sends = 0

    while offset < samples.size and RUNNING:
        sent = streamer.send(samples[offset:], metadata)
        metadata.start_of_burst = False

        if sent == 0:
            zero_sends += 1
            time.sleep(0.001)
            continue

        if sent < 0 or sent > samples.size - offset:
            raise RuntimeError(f"UHD devolvió un número inválido: {sent}")

        offset += sent

    return offset, zero_sends


def main() -> int:
    args = parse_args()
    validate_args(args)

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    center_frequency = CHANNEL_TO_FREQ_HZ[args.wifi_channel]
    superframe, mpdu = build_superframe(args)
    save_reference(args.save_npz, superframe, mpdu, args)

    print("Beacon Wi-Fi visible mediante USRP")
    print(f"  SSID                    : {args.ssid}")
    print(f"  BSSID                   : {args.bssid}")
    print(f"  Canal Wi-Fi             : {args.wifi_channel}")
    print(f"  Frecuencia central      : {center_frequency / 1e6:.3f} MHz")
    print(f"  Canal lógico B210       : {args.channel_index}")
    print(f"  Antena                  : TX/RX")
    print(f"  Sample rate             : {SAMPLE_RATE / 1e6:.3f} Msps")
    print(f"  Beacon interval         : {BEACON_INTERVAL_TU} TU")
    print(f"  Periodo                 : {PERIOD_SECONDS * 1e3:.3f} ms")
    print(f"  Periodos/superframe     : {args.periods_per_superframe}")
    print(f"  Superframe samples      : {superframe.size}")
    print(f"  Superframe duration     : {superframe.size / SAMPLE_RATE:.4f} s")
    print(f"  MPDU bytes              : {len(mpdu)}")
    print(f"  Pico digital            : {np.max(np.abs(superframe)):.3f}")
    print(f"  Referencia guardada     : {args.save_npz}")

    usrp, streamer = configure_usrp(args)

    print("\nConfiguración efectiva:")
    print(
        f"  Rate       : "
        f"{usrp.get_tx_rate(args.channel_index) / 1e6:.6f} Msps"
    )
    print(
        f"  Frequency  : "
        f"{usrp.get_tx_freq(args.channel_index) / 1e6:.6f} MHz"
    )
    print(f"  Gain       : {usrp.get_tx_gain(args.channel_index):.2f} dB")
    print(f"  Antenna    : {usrp.get_tx_antenna(args.channel_index)}")

    metadata = uhd.types.TXMetadata()
    metadata.has_time_spec = False
    metadata.start_of_burst = True
    metadata.end_of_burst = False

    superframes = 0
    total_zero_sends = 0
    last_report = time.monotonic()

    print("\nTransmitiendo. Pulsa Ctrl+C para detener.")
    print(
        "En el otro PC ejecuta:\n"
        "  nmcli device wifi rescan ifname wlp4s0\n"
        "  nmcli -f SSID,BSSID,CHAN,FREQ,SIGNAL "
        "device wifi list ifname wlp4s0"
    )

    while RUNNING:
        sent, zero_sends = send_all(streamer, superframe, metadata)
        total_zero_sends += zero_sends

        if sent == superframe.size:
            superframes += 1

        now = time.monotonic()
        if now - last_report >= 1.0:
            beacons_sent = superframes * args.periods_per_superframe
            print(
                f"superframes={superframes} "
                f"beacons={beacons_sent} "
                f"zero_sends={total_zero_sends}"
            )
            last_report = now

    end_metadata = uhd.types.TXMetadata()
    end_metadata.has_time_spec = False
    end_metadata.start_of_burst = False
    end_metadata.end_of_burst = True

    try:
        streamer.send(
            np.zeros((1, 0), dtype=np.complex64),
            end_metadata,
        )
    except RuntimeError as exc:
        print(f"AVISO al cerrar EOB: {exc}")

    print("\nTX detenido.")
    print(f"Superframes completos: {superframes}")
    print(f"Zero sends totales: {total_zero_sends}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
