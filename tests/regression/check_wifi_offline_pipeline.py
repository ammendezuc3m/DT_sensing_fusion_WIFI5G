#!/usr/bin/env python3

import argparse
import json
import math
import re
import sys
from pathlib import Path


EXPECTED_COUNTERS = [
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    11, 12, 13, 15, 16, 17, 18, 19, 20,
    21, 22, 23, 24, 25, 26, 27, 28, 29,
    30, 31, 32, 33, 34, 35, 36, 37, 38,
    40, 41, 43, 44, 45, 46, 47, 48, 49,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Valida la regresión del pipeline "
            "offline WiFi Non-HT."
        )
    )

    parser.add_argument(
        "--log",
        type=Path,
        required=True,
        help="Log generado por offline_waveform_pipeline",
    )

    parser.add_argument(
        "--features",
        type=Path,
        required=True,
        help="Fichero JSONL de FeatureFrame",
    )

    return parser.parse_args()


def extract_stat(text: str, label: str) -> int:
    pattern = re.compile(
        rf"^{re.escape(label)}\s*:\s*(\d+)\s*$",
        re.MULTILINE,
    )

    match = pattern.search(text)

    if match is None:
        raise ValueError(
            f"No se encontró la estadística: {label}"
        )

    return int(match.group(1))


def main() -> int:
    args = parse_args()

    if not args.log.is_file():
        print(
            f"ERROR: no existe el log: {args.log}",
            file=sys.stderr,
        )
        return 1

    if not args.features.is_file():
        print(
            f"ERROR: no existe el JSONL: {args.features}",
            file=sys.stderr,
        )
        return 1

    log_text = args.log.read_text(
        encoding="utf-8",
        errors="replace",
    )

    expected_stats = {
        "Muestras procesadas": 140_000_000,
        "Sincronizados": 48,
        "Decodificados": 47,
        "Publicados": 46,
        "Frames escritos": 46,
    }

    errors: list[str] = []

    for label, expected in expected_stats.items():
        try:
            actual = extract_stat(log_text, label)
        except ValueError as error:
            errors.append(str(error))
            continue

        if actual != expected:
            errors.append(
                f"{label}: esperado={expected}, "
                f"obtenido={actual}"
            )

    frames = []

    for line_number, line in enumerate(
        args.features.read_text(
            encoding="utf-8"
        ).splitlines(),
        start=1,
    ):
        if not line.strip():
            continue

        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError as error:
            errors.append(
                f"JSON inválido en línea "
                f"{line_number}: {error}"
            )

    if len(frames) != 46:
        errors.append(
            f"Frames JSONL: esperado=46, "
            f"obtenido={len(frames)}"
        )

    counters = []
    maximum_csi = 0.0

    for frame_index, frame in enumerate(frames):
        if frame.get("waveform_type") != 1:
            errors.append(
                f"Frame {frame_index}: "
                f"waveform_type inválido"
            )

        if frame.get("profile_id") != 1:
            errors.append(
                f"Frame {frame_index}: "
                f"profile_id inválido"
            )

        if frame.get("transmitter_id") != 1:
            errors.append(
                f"Frame {frame_index}: "
                f"transmitter_id inválido"
            )

        if frame.get("experiment_id") != 1:
            errors.append(
                f"Frame {frame_index}: "
                f"experiment_id inválido"
            )

        counters.append(
            frame.get("packet_counter")
        )

        metadata = frame.get(
            "text_metadata",
            {},
        )

        if metadata.get("ssid") != "USRP_CHANNEL11":
            errors.append(
                f"Frame {frame_index}: SSID inválido"
            )

        if (
            metadata.get("bssid")
            != "02:11:22:33:44:55"
        ):
            errors.append(
                f"Frame {frame_index}: BSSID inválido"
            )

        if metadata.get("vendor_magic") != "ALBSENS":
            errors.append(
                f"Frame {frame_index}: "
                f"vendor_magic inválido"
            )

        csi = frame.get("complex_features", [])

        if len(csi) != 52:
            errors.append(
                f"Frame {frame_index}: "
                f"CSI esperado=52, "
                f"obtenido={len(csi)}"
            )
            continue

        for csi_index, value in enumerate(csi):
            try:
                magnitude = math.hypot(
                    float(value["real"]),
                    float(value["imag"]),
                )
            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                errors.append(
                    f"Frame {frame_index}, CSI "
                    f"{csi_index}: formato inválido"
                )
                continue

            if not math.isfinite(magnitude):
                errors.append(
                    f"Frame {frame_index}, CSI "
                    f"{csi_index}: valor no finito"
                )

            if magnitude > 10.0:
                errors.append(
                    f"Frame {frame_index}, CSI "
                    f"{csi_index}: magnitud={magnitude}"
                )

            maximum_csi = max(
                maximum_csi,
                magnitude,
            )

    if counters != EXPECTED_COUNTERS:
        errors.append(
            "Secuencia de packet_counter distinta "
            "de la captura golden"
        )

    if errors:
        print("REGRESIÓN FALLIDA")

        for error in errors:
            print(f"  - {error}")

        return 1

    print("REGRESIÓN CORRECTA")
    print(f"Frames: {len(frames)}")
    print("CSI por frame: 52")
    print(f"Máximo CSI: {maximum_csi:.6f}")
    print(f"Contadores: {counters}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
