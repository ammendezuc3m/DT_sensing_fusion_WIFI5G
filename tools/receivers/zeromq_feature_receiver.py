#!/usr/bin/env python3

import argparse
import json
import signal
import time
from typing import Any

import zmq


running = True


def handle_signal(
    _signum: int,
    _frame: Any,
) -> None:
    global running
    running = False


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Receptor ZeroMQ PULL para "
            "FeatureFrame"
        )
    )

    parser.add_argument(
        "--bind",
        default="tcp://127.0.0.1:5555",
        help=(
            "Endpoint en el que el receptor "
            "realiza bind"
        ),
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    signal.signal(
        signal.SIGINT,
        handle_signal,
    )

    signal.signal(
        signal.SIGTERM,
        handle_signal,
    )

    context = zmq.Context()
    socket = context.socket(zmq.PULL)

    socket.setsockopt(
        zmq.LINGER,
        0,
    )

    socket.bind(arguments.bind)

    poller = zmq.Poller()
    poller.register(
        socket,
        zmq.POLLIN,
    )

    print(
        f"ZeroMQ PULL escuchando en "
        f"{arguments.bind}"
    )

    received = 0
    first_counter = None
    previous_counter = None
    start_time = time.monotonic()

    try:
        while running:
            events = dict(
                poller.poll(timeout=250)
            )

            if socket not in events:
                continue

            message = socket.recv_string()
            frame = json.loads(message)

            received += 1

            counter = int(
                frame["packet_counter"]
            )

            if first_counter is None:
                first_counter = counter

            gap = None

            if (
                previous_counter is not None
                and counter != previous_counter + 1
            ):
                gap = (
                    previous_counter,
                    counter,
                )

            previous_counter = counter

            csi = frame.get(
                "complex_features",
                [],
            )

            print(
                "RX_FEATURE"
                f" | counter={counter}"
                f" | tx={frame['transmitter_id']}"
                f" | exp={frame['experiment_id']}"
                f" | CSI={len(csi)}"
                f" | SNR={frame['snr_db']:.2f} dB"
                f" | CFO={frame['cfo_hz']:.2f} Hz"
                f" | gap={gap}"
            )

    finally:
        elapsed = (
            time.monotonic()
            - start_time
        )

        print()
        print("Recepción finalizada")
        print("Frames:", received)
        print("Primer contador:", first_counter)
        print("Último contador:", previous_counter)
        print("Duración:", round(elapsed, 3), "s")

        socket.close()
        context.term()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
