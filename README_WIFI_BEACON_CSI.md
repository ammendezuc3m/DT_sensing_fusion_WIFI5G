# WiFi Beacon CSI Quick Start

This document explains how to run the WiFi beacon CSI part of the repository.

The WiFi pipeline uses two USRP B210 devices:

```text
PC2 / TX USRP
  -> transmits real 802.11a/g legacy OFDM beacon frames every 100 ms

PC1 / RX USRP
  -> receives at 20 Msps
  -> tracks beacons using the L-LTF repeated-symbol structure
  -> extracts CSI from 52 active OFDM subcarriers
  -> saves H5 / CSV / metadata
  -> writes a live JSON state
```

The current setup is designed for controlled lab experiments and dataset collection. It is not intended to act as a production WiFi access point.

---

## 1. Current status

Tested behavior:

```text
TX: 802.11a/g beacon waveform transmitted every 100 ms
RX offline: 50/50 CSI detections in a 5 s raw capture
RX online: 565 CSI packets in a 60 s run with 0 RX overflows
```

Online RX quality observed in the tested setup:

```text
CSI rate: about 9.5 CSI/s
period mean: about 0.100 s
overflow_count: 0
```

---

## 2. Hardware setup

Recommended roles:

```text
PC1 = RX computer
PC2 = TX computer
```

USRP antenna ports used in the tested setup:

```text
PC1 RX antenna: RX2
PC2 TX antenna: TX/RX
```

Check serials on each computer:

```bash
uhd_find_devices
```

Use the serial reported by UHD in the commands below.

---

## 3. Environment

On each computer:

```bash
cd DT_sensing_fusion_WIFI5G
python3 -m venv --system-site-packages .venv_uhd
source .venv_uhd/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/requirements-uhd.txt
```

Check imports:

```bash
python - <<'PY'
import uhd
import numpy
import h5py
print("uhd OK")
print("numpy", numpy.__version__)
print("h5py OK")
PY
```

---

## 4. PC2: WiFi beacon TX

Start TX:

```bash
cd DT_sensing_fusion_WIFI5G
source .venv_uhd/bin/activate

python src/python/wifi_sensing/tx_wifi_beacon_usrp.py \
  --serial <tx_usrp_serial>
```

The script defaults are already set for the tested WiFi sensing configuration:

```text
freq = 2.412 GHz
sample rate = 20 Msps
gain = 20 dB
antenna = TX/RX
SSID = SENSING_WIFI
BSSID = 02:11:22:33:44:55
WiFi channel = 1
PHY = 802.11a/g legacy OFDM
rate = 6 Mb/s BPSK 1/2
TX period = 100.000 ms
Beacon Interval field = 98 TU = 100.352 ms
profile = router_like_wpa2
num beacons = 5000
```

Expected TX output:

```text
First beacon samples: 4160
First beacon duration: 208.000 us
sent=4160
zero_sends=0
```

If `progress-every` is left at the default, the script prints every 50 beacons.

---

## 5. PC1: WiFi online CSI RX

Start RX:

```bash
cd DT_sensing_fusion_WIFI5G
source .venv_uhd/bin/activate

python src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py \
  --serial "<rx_usrp_serial>,num_recv_frames=512,recv_frame_size=8200"
```

The script defaults are already set for the tested online RX configuration:

```text
freq = 2.412 GHz
sample rate = 20 Msps
gain = 35 dB
antenna = RX2
duration = 60 s
block-ms = 200
queue-blocks = 8
max-drain-blocks = 4
init-seconds = 1
TX period = 100 ms
seed-threshold = 0.10
accept-threshold = 0.10
search-radius-ms = 5
buffer-keep-sec = 0.5
output-root = data/wifi_csi_datasets
local JSON = results/wifi_online/live_wifi_rx_state.json
```

Expected output for a good 60 s run:

```text
Detected CSI packets: about 540-590
Missed beacons: low
overflow_count: 0
```

The exact count depends on signal strength, antenna placement, multipath, and whether the receiver misses some beacons during initialization/tracking.

---

## 6. Watch the live JSON

In another terminal on PC1:

```bash
watch -n 0.5 cat results/wifi_online/live_wifi_rx_state.json
```

Important JSON fields:

```text
status
packets_detected
missed_beacons
phase_samples
period_samples
last_packet.ltf_metric
last_packet.cfo_hz
last_packet.rx_power_db
last_packet.csi_shape
last_packet.inference
rx_stats.overflow_count
rx_stats.dropped_blocks
```

---

## 7. Output dataset format

Each RX run creates:

```text
data/wifi_csi_datasets/<label>/<session_id>/
  session_data.h5
  metadata.json
  capture_log.csv
```

Main H5 datasets:

```text
csi                  complex64, shape [N, 52]
csi_amp              float32,   shape [N, 52]
csi_phase            float32,   shape [N, 52]
timestamp_unix       float64,   shape [N]
timestamp_usrp_rx    float64,   shape [N]
packet_index         int64,     shape [N]
timing_offset_samples int64,    shape [N]
cfo_hz               float32,   shape [N]
ltf_metric           float32,   shape [N]
rx_power_db          float32,   shape [N]
```

The CSI vector has 52 entries because the legacy OFDM symbol uses 52 active subcarriers:

```text
48 data subcarriers + 4 pilot subcarriers
```

---

## 8. Quick H5 inspection

```bash
LAST_H5="$(find data/wifi_csi_datasets -name session_data.h5 | sort | tail -n 1)"

python - <<'PY'
import os
import h5py
import numpy as np

p = os.environ.get("LAST_H5")

with h5py.File(p, "r") as h5:
    print("file:", p)
    print("csi:", h5["csi"].shape)

    m = h5["ltf_metric"][:]
    print("metric mean:", np.mean(m) if len(m) else None)
    print("metric min/max:", (np.min(m), np.max(m)) if len(m) else None)
    print("metric > 0.5:", np.sum(m > 0.5), "/", len(m))

    t = h5["timestamp_unix"][:]
    if len(t) > 1:
        dt = np.diff(t)
        print("period mean:", np.mean(dt))
        print("period std:", np.std(dt))
        print("first periods:", dt[:10])
PY
```

A good run should have:

```text
period mean close to 0.100 s
overflow_count = 0 in the final RX stats
```

---

## 9. Inference options

The online RX currently supports:

```text
--inference-backend none
--inference-backend threshold
```

Default:

```text
--inference-backend none
```

This writes:

```text
label = UNTRAINED
confidence = 0.0
```

Placeholder threshold inference:

```bash
python src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py \
  --serial "<rx_usrp_serial>,num_recv_frames=512,recv_frame_size=8200" \
  --inference-backend threshold
```

This is not a trained model. It is only useful to test that the JSON contains label/probability/confidence fields before a real WiFi model is available.

Future model flow:

```text
1. Collect labeled WiFi CSI sessions.
2. Train a model using csi_amp/csi_phase or complex CSI features.
3. Add/use a torch backend for WiFi inference.
4. Run online RX with --model-path pointing to the trained checkpoint.
```

---

## 10. Offline raw capture fallback

For the cleanest dataset or for debugging, use raw IQ capture followed by offline L-LTF tracking.

### 10.1 Capture raw IQ on PC1

Start PC2 TX first. Then on PC1:

```bash
python src/python/wifi_sensing/rx_wifi_raw_capture_usrp.py \
  --serial <rx_usrp_serial> \
  --freq 2.412e9 \
  --rate 20e6 \
  --gain 35 \
  --antenna RX2 \
  --duration-sec 5 \
  --block-ms 50 \
  --output-npy results/wifi_debug/raw_wifi_5s.npy
```

A good raw capture should show:

```text
overflow_count: 0
```

### 10.2 Process raw IQ offline

```bash
python src/python/wifi_sensing/process_wifi_raw_capture_ltf_tracker.py \
  --input-npy results/wifi_debug/raw_wifi_5s.npy \
  --rate 20e6 \
  --tx-period-ms 100.0 \
  --seed-threshold 0.10 \
  --accept-threshold 0.10 \
  --search-radius-ms 5 \
  --output-h5 results/wifi_debug/raw_wifi_5s_ltf_tracker_csi.h5
```

Expected result for a clean 5 s capture:

```text
expected: 50
detections: close to 50
period mean: close to 0.100 s
```

---

## 11. Important implementation details

### 11.1 Why L-LTF tracking is used

The receiver does not decode the full 802.11 MAC frame. It detects beacon timing and extracts CSI using the legacy WiFi preamble, specifically the L-LTF.

The L-LTF contains two repeated 64-sample long training symbols. The tracker correlates those repeated symbols to find packet starts robustly under channel distortion and multipath.

### 11.2 Why not scan the full signal continuously

A naive full-stream detector was too expensive for 20 Msps in Python. The current online method is:

```text
1. Collect a short initialization buffer.
2. Detect seed beacons.
3. Estimate the beacon phase modulo 100 ms.
4. Track future beacons at phase + n * 100 ms.
5. Search only a small window around each expected beacon.
```

This is why the online receiver can run close to 10 CSI/s in Python.

### 11.3 PHY/MAC limitations

The TX waveform is generated by software and transmitted by USRP. It is intended for CSI experiments. It is not a full WiFi AP stack:

```text
No association
No IP
No DHCP
No CSMA/CA
No hostapd
No traffic data frames
```

The transmitted management frame is a beacon-like 802.11 frame with realistic fields for the experiment.

---

## 12. Git policy

Do not commit generated WiFi outputs:

```text
data/wifi_csi_datasets/
results/wifi_debug/
results/wifi_online/
__pycache__/
*.pyc
```

Commit only code, configs, docs, and intentional model artifacts.
