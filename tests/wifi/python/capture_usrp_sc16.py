#!/usr/bin/env python3

import argparse
import time
from pathlib import Path

import numpy as np
import uhd


def parse_args():
    parser = argparse.ArgumentParser(
        description="Captura IQ desde una USRP B210 y guarda sc16."
    )

    parser.add_argument("--serial", required=True)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--freq", type=float, required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--gain", type=float, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--output", required=True)

    return parser.parse_args()


def main():
    args = parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Creando USRP...")
    usrp = uhd.usrp.MultiUSRP(f"serial={args.serial}")

    channel = args.channel

    usrp.set_rx_rate(args.rate, channel)
    usrp.set_rx_freq(
        uhd.types.TuneRequest(args.freq),
        channel,
    )
    usrp.set_rx_gain(args.gain, channel)
    usrp.set_rx_antenna("RX2", channel)

    actual_rate = usrp.get_rx_rate(channel)
    actual_freq = usrp.get_rx_freq(channel)
    actual_gain = usrp.get_rx_gain(channel)
    actual_ant = usrp.get_rx_antenna(channel)

    print("==========================================")
    print("Captura USRP B210")
    print("==========================================")
    print(f"Serial             : {args.serial}")
    print(f"Canal lógico RX    : {channel}")
    print(f"Antena             : {actual_ant}")
    print(f"Frecuencia         : {actual_freq / 1e6:.6f} MHz")
    print(f"Sample rate        : {actual_rate / 1e6:.6f} Msps")
    print(f"Ganancia           : {actual_gain:.2f} dB")
    print(f"Duración           : {args.duration:.3f} s")
    print(f"Archivo            : {output_path}")
    print()

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [channel]

    streamer = usrp.get_rx_stream(stream_args)

    max_samples = streamer.get_max_num_samps()
    recv_buffer = np.zeros(max_samples, dtype=np.complex64)

    metadata = uhd.types.RXMetadata()

    stream_command = uhd.types.StreamCMD(
        uhd.types.StreamMode.start_cont
    )
    stream_command.stream_now = True
    streamer.issue_stream_cmd(stream_command)

    requested_samples = int(round(args.duration * actual_rate))

    total_samples = 0
    timeout_count = 0
    overflow_count = 0

    start_time = time.monotonic()
    last_report = start_time

    with output_path.open("wb") as output_file:
        while total_samples < requested_samples:
            remaining = requested_samples - total_samples
            requested_now = min(max_samples, remaining)

            received = streamer.recv(
                recv_buffer,
                metadata,
                timeout=1.0,
            )

            if metadata.error_code == uhd.types.RXMetadataErrorCode.timeout:
                timeout_count += 1
                print("Timeout RX")
                continue

            if metadata.error_code == uhd.types.RXMetadataErrorCode.overflow:
                overflow_count += 1
                continue

            if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
                print(
                    "Error RX:",
                    metadata.strerror(),
                )
                continue

            if received <= 0:
                continue

            received = min(received, requested_now)
            iq = recv_buffer[:received]

            # Convertir complex float [-1,1] a sc16 intercalado:
            # I0,Q0,I1,Q1,...
            scaled_i = np.clip(
                np.real(iq) * 32767.0,
                -32768,
                32767,
            ).astype("<i2")

            scaled_q = np.clip(
                np.imag(iq) * 32767.0,
                -32768,
                32767,
            ).astype("<i2")

            interleaved = np.empty(received * 2, dtype="<i2")
            interleaved[0::2] = scaled_i
            interleaved[1::2] = scaled_q

            interleaved.tofile(output_file)
            total_samples += received

            now = time.monotonic()

            if now - last_report >= 1.0:
                print(
                    f"muestras={total_samples}/{requested_samples} "
                    f"overflows={overflow_count} "
                    f"timeouts={timeout_count}"
                )
                last_report = now

    stop_command = uhd.types.StreamCMD(
        uhd.types.StreamMode.stop_cont
    )
    streamer.issue_stream_cmd(stop_command)

    elapsed = time.monotonic() - start_time
    file_size_mb = output_path.stat().st_size / 1024**2

    print()
    print("==========================================")
    print("Captura terminada")
    print("==========================================")
    print(f"Muestras recibidas : {total_samples}")
    print(f"Duración real      : {elapsed:.3f} s")
    print(f"Overflows          : {overflow_count}")
    print(f"Timeouts           : {timeout_count}")
    print(f"Tamaño archivo     : {file_size_mb:.2f} MiB")


if __name__ == "__main__":
    main()
