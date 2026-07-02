#!/usr/bin/env python3
"""
Dataset writer for WiFi beacon CSI sessions.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np


def utc_session_id(label: str, scene: str = "static") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    safe_scene = "".join(c if c.isalnum() or c in "-_" else "_" for c in scene)

    return f"session_{ts}_{safe_label}_{safe_scene}"


@dataclass
class WifiCsiSessionMeta:
    session_id: str
    label: str
    scene: str
    ssid_filter: str
    bssid_filter: str
    freq_hz: float
    sample_rate_hz: float
    gain_db: float
    channel: int
    beacon_interval_tu: int
    tx_period_ms: float | None
    notes: str = ""


class WifiCsiDatasetWriter:
    def __init__(self, output_root: str | Path, meta: WifiCsiSessionMeta):
        self.output_root = Path(output_root)
        self.meta = meta
        self.session_dir = self.output_root / meta.label / meta.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.h5_path = self.session_dir / "session_data.h5"
        self.csv_path = self.session_dir / "capture_log.csv"
        self.meta_path = self.session_dir / "metadata.json"

        self.rows: list[dict[str, Any]] = []

        self.csi: list[np.ndarray] = []
        self.csi_amp: list[np.ndarray] = []
        self.csi_phase: list[np.ndarray] = []
        self.timestamp_unix: list[float] = []
        self.timestamp_usrp_rx: list[float] = []
        self.packet_index: list[int] = []
        self.timing_offset_samples: list[int] = []
        self.cfo_hz: list[float] = []
        self.ltf_metric: list[float] = []
        self.rx_power_db: list[float] = []

        self.meta_path.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")

    def append(
        self,
        *,
        csi: np.ndarray,
        timestamp_unix: float,
        timestamp_usrp_rx: float,
        packet_index: int,
        timing_offset_samples: int,
        cfo_hz: float,
        ltf_metric: float,
        rx_power_db: float,
    ) -> None:

        csi = np.asarray(csi, dtype=np.complex64)

        self.csi.append(csi)
        self.csi_amp.append(np.abs(csi).astype(np.float32))
        self.csi_phase.append(np.angle(csi).astype(np.float32))

        self.timestamp_unix.append(float(timestamp_unix))
        self.timestamp_usrp_rx.append(float(timestamp_usrp_rx))
        self.packet_index.append(int(packet_index))
        self.timing_offset_samples.append(int(timing_offset_samples))
        self.cfo_hz.append(float(cfo_hz))
        self.ltf_metric.append(float(ltf_metric))
        self.rx_power_db.append(float(rx_power_db))

        row = {
            "packet_index": int(packet_index),
            "timestamp_unix": float(timestamp_unix),
            "timestamp_usrp_rx": float(timestamp_usrp_rx),
            "timing_offset_samples": int(timing_offset_samples),
            "cfo_hz": float(cfo_hz),
            "ltf_metric": float(ltf_metric),
            "rx_power_db": float(rx_power_db),
        }

        self.rows.append(row)

    def flush(self) -> None:
        if self.csi:
            csi_arr = np.stack(self.csi, axis=0).astype(np.complex64)
            amp_arr = np.stack(self.csi_amp, axis=0).astype(np.float32)
            phase_arr = np.stack(self.csi_phase, axis=0).astype(np.float32)
        else:
            csi_arr = np.zeros((0, 52), dtype=np.complex64)
            amp_arr = np.zeros((0, 52), dtype=np.float32)
            phase_arr = np.zeros((0, 52), dtype=np.float32)

        with h5py.File(self.h5_path, "w") as h5:
            h5.create_dataset("csi", data=csi_arr)
            h5.create_dataset("csi_amp", data=amp_arr)
            h5.create_dataset("csi_phase", data=phase_arr)
            h5.create_dataset("timestamp_unix", data=np.asarray(self.timestamp_unix, dtype=np.float64))
            h5.create_dataset("timestamp_usrp_rx", data=np.asarray(self.timestamp_usrp_rx, dtype=np.float64))
            h5.create_dataset("packet_index", data=np.asarray(self.packet_index, dtype=np.int64))
            h5.create_dataset("timing_offset_samples", data=np.asarray(self.timing_offset_samples, dtype=np.int64))
            h5.create_dataset("cfo_hz", data=np.asarray(self.cfo_hz, dtype=np.float32))
            h5.create_dataset("ltf_metric", data=np.asarray(self.ltf_metric, dtype=np.float32))
            h5.create_dataset("rx_power_db", data=np.asarray(self.rx_power_db, dtype=np.float32))

            for k, v in asdict(self.meta).items():
                h5.attrs[k] = "" if v is None else v

        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "packet_index",
                "timestamp_unix",
                "timestamp_usrp_rx",
                "timing_offset_samples",
                "cfo_hz",
                "ltf_metric",
                "rx_power_db",
            ]

            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()

            for row in self.rows:
                w.writerow(row)

    def close(self) -> None:
        self.flush()
