#!/usr/bin/env python3

from __future__ import annotations

import argparse
import time

import numpy as np
import uhd

from src.python.wifi_sensing.tx_wifi_usrp import build_vendor_specific_ie
from src.python.wifi_sensing.wifi_legacy_ofdm import make_beacon_waveform


SAMPLE_RATE = 20e6
BEACON_INTERVAL_TU = 100
PERIOD_SECONDS = BEACON_INTERVAL_TU * 1024e-6
PERIOD_SAMPLES = int(round(PERIOD_SECONDS * SAMPLE_RATE))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default="34B739F")
    p.add_argument("--channel-index", type=int, default=0)
    p.add_argument("--wifi-channel", type=int, default=11)
    p.add_argument("--ssid", default="USRP_CHANNEL11")
    p.add_argument("--bssid", default="02:11:22:33:44:55")
    p.add_argument("--gain", type=float, default=76.0)
    p.add_argument("--amplitude", type=float, default=0.5)
    p.add_argument("--num-beacons", type=int, default=50)
    return p.parse_args()


def channel_frequency(channel):
    return (2407 + 5 * channel) * 1e6


def main():
    args = parse_args()

    center_frequency = channel_frequency(args.wifi_channel)

    total_samples = PERIOD_SAMPLES * args.num_beacons
    tx_buffer = np.zeros(total_samples, dtype=np.complex64)

    print("Generando buffer completo...")

    for counter in range(args.num_beacons):
        vendor_ie = build_vendor_specific_ie(
            oui=bytes.fromhex("021122"),
            vendor_type=1,
            magic=b"ALBSENS",
            version=1,
            transmitter_id=1,
            experiment_id=1,
            packet_counter=counter,
        )

        waveform, _ = make_beacon_waveform(
            ssid=args.ssid,
            bssid=args.bssid,
            channel=args.wifi_channel,
            beacon_interval_tu=BEACON_INTERVAL_TU,
            sequence_number=counter & 0xFFF,
            timestamp_us=int(round(counter * PERIOD_SECONDS * 1e6)),
            profile="router_like_wpa2",
            extra_ies=[vendor_ie],
        )

        waveform = np.asarray(waveform, dtype=np.complex64).reshape(-1)

        peak = float(np.max(np.abs(waveform)))
        if peak <= 0:
            raise RuntimeError("Waveform con amplitud cero")

        waveform *= np.float32(args.amplitude / peak)

        start = counter * PERIOD_SAMPLES
        end = start + waveform.size

        tx_buffer[start:end] = waveform

        print(
            f"Preparado beacon {counter+1:3d}/{args.num_beacons} "
            f"| seq={counter & 0xFFF}"
        )

    print()
    print(f"Duración buffer: {tx_buffer.size / SAMPLE_RATE:.3f} s")
    print(f"Muestras totales: {tx_buffer.size}")

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

    metadata = uhd.types.TXMetadata()
    metadata.has_time_spec = False
    metadata.start_of_burst = True
    metadata.end_of_burst = False

    print()
    print("Transmitiendo buffer completo...")

    offset = 0
    zero_sends = 0

    while offset < tx_buffer.size:
        sent = streamer.send(tx_buffer[offset:], metadata)
        metadata.start_of_burst = False

        if sent == 0:
            zero_sends += 1
            time.sleep(0.001)
            continue

        offset += sent

        if offset % (PERIOD_SAMPLES * 5) < sent:
            estimated = min(
                args.num_beacons,
                offset // PERIOD_SAMPLES,
            )
            print(
                f"Progreso aproximado: "
                f"{estimated}/{args.num_beacons} beacons"
            )

    end_metadata = uhd.types.TXMetadata()
    end_metadata.has_time_spec = False
    end_metadata.start_of_burst = False
    end_metadata.end_of_burst = True

    streamer.send(
        np.zeros((1, 0), dtype=np.complex64),
        end_metadata,
    )

    print()
    print("TX terminado")
    print(f"Beacons incluidos : {args.num_beacons}")
    print(f"Zero sends        : {zero_sends}")


if __name__ == "__main__":
    main()
