# DT_sensing_fusion_WIFI5G

Integrated 5G SSB and WiFi beacon CSI sensing repository using USRP B210 radios.

This repository contains two complementary sensing pipelines:

```text
1. 5G SSB sensing
   Passive receiver pipeline based on 5G SSB/rxGridSSB features.

2. WiFi beacon CSI sensing
   Active TX/RX pipeline where one USRP transmits 802.11a/g beacon frames and
   another USRP extracts CSI from the received WiFi L-LTF.
```

The recommended deployment path is Python-only. MATLAB material is kept only as historical/reference material for the original 5G validation workflow.

---

## 1. Repository scope

### 1.1 5G SSB sensing

The 5G path receives SSB bursts from a 5G source/cell and extracts a complex SSB grid for inference.

```text
USRP B210 RX
  -> Python UHD IQ capture
  -> CFO warmup/correction
  -> PSS/NID2/timing detection
  -> OFDM demodulation
  -> dataSSB / rxGridSSB extraction
  -> PyTorch model inference
  -> local JSON
  -> optional Mitsuba/Sionna XML
  -> optional SCP to a remote Digital Twin machine
```

Main online script:

```text
src/python/ssb_python/online_5g_python_cfo_json_scp.py
```

Current demo checkpoint:

```text
results/binary_empty_vs_P5_rx/model_rxGridSSB/model.pt
```

Current demo classes:

```text
empty
P5
```

Important: this checkpoint is a demonstration model for the original lab setup. It is not a general human detector and should be retrained for a new room, factory, antenna placement, receiver location, or class set.

---

### 1.2 WiFi beacon CSI sensing

The WiFi path uses two USRP B210 devices and two computers.

```text
PC2 / USRP B210 TX
  -> generate real 802.11a/g legacy OFDM beacon frame
  -> transmit one beacon every 100 ms

PC1 / USRP B210 RX
  -> receive IQ at 20 Msps
  -> detect beacon timing using L-LTF repetition
  -> extract 52-subcarrier CSI from the L-LTF
  -> save H5 / CSV / metadata
  -> write live JSON
  -> optional placeholder threshold inference
```

Main TX script:

```text
src/python/wifi_sensing/tx_wifi_beacon_usrp.py
```

Main online RX script:

```text
src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py
```

Main WiFi documentation:

```text
README_WIFI_BEACON_CSI.md
docs/WIFI_BEACON_CSI_PIPELINE.md
```

Current tested WiFi PHY/MAC setup:

```text
802.11a/g legacy OFDM
20 MHz channel
6 Mb/s BPSK 1/2
SSID: SENSING_WIFI
BSSID: 02:11:22:33:44:55
physical TX period: 100.000 ms
Beacon Interval field: 98 TU = 100.352 ms
```

The physical transmission timing is controlled by the USRP timed TX loop. The 98 TU Beacon Interval field is the nearest standard beacon interval field value to 100 ms.

---

## 2. Hardware roles

The current recommended two-PC setup is:

```text
PC1 = receiver
  - WiFi CSI RX
  - future combined 5G RX + WiFi RX/synchronization logic

PC2 = transmitter
  - WiFi beacon TX
```

Both computers can run both scripts after pulling the same repository version, but the tested default assumptions are:

```text
PC1 RX antenna: RX2
PC2 TX antenna: TX/RX
```

Use the real serial numbers reported by:

```bash
uhd_find_devices
```

---

## 3. Fresh computer setup

### 3.1 Clone

```bash
git clone git@github.com:ammendezuc3m/DT_sensing_fusion_WIFI5G.git
cd DT_sensing_fusion_WIFI5G
```

If SSH is not configured, use the HTTPS clone URL and authenticate with a GitHub token.

### 3.2 Install system dependencies

```bash
sudo apt update
sudo apt install -y \
  git \
  python3 \
  python3-pip \
  python3-venv \
  uhd-host \
  python3-uhd
```

Check the USRP:

```bash
uhd_find_devices
uhd_usrp_probe
```

### 3.3 Create the UHD Python environment

The UHD Python binding is provided by the OS package, so the virtual environment must expose system site packages.

```bash
python3 -m venv --system-site-packages .venv_uhd
source .venv_uhd/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/requirements-uhd.txt
```

If the requirements file is unavailable, install the minimal dependencies:

```bash
python -m pip install "numpy<2" scipy h5py matplotlib torch
```

Check imports:

```bash
python - <<'PY'
import numpy
import h5py
import uhd
print("numpy:", numpy.__version__)
print("h5py OK")
print("uhd OK")
PY
```

---

## 4. Quick start: WiFi beacon CSI

### 4.1 PC2: start WiFi beacon TX

```bash
cd DT_sensing_fusion_WIFI5G
source .venv_uhd/bin/activate

python src/python/wifi_sensing/tx_wifi_beacon_usrp.py \
  --serial <tx_usrp_serial>
```

The useful TX defaults are already configured in the script:

```text
freq = 2.412 GHz
rate = 20 Msps
gain = 20 dB
antenna = TX/RX
SSID = SENSING_WIFI
BSSID = 02:11:22:33:44:55
WiFi channel = 1
TX period = 100 ms
Beacon Interval field = 98 TU
profile = router_like_wpa2
num beacons = 5000
```

### 4.2 PC1: start WiFi online CSI RX

```bash
cd DT_sensing_fusion_WIFI5G
source .venv_uhd/bin/activate

python src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py \
  --serial "<rx_usrp_serial>,num_recv_frames=512,recv_frame_size=8200"
```

The useful RX defaults are already configured in the script:

```text
freq = 2.412 GHz
rate = 20 Msps
gain = 35 dB
antenna = RX2
duration = 60 s
block-ms = 200
queue-blocks = 8
max-drain-blocks = 4
init-seconds = 1
TX period = 100 ms
seed/accept threshold = 0.10
search radius = 5 ms
output root = data/wifi_csi_datasets
live JSON = results/wifi_online/live_wifi_rx_state.json
```

A good 60 s online run should produce roughly:

```text
~540-590 CSI packets
0 RX overflows
period mean close to 0.100 s
```

Example observed result in the tested setup:

```text
Detected CSI packets: 565
Missed beacons: 32
overflow_count: 0
```

### 4.3 Watch the WiFi live JSON

```bash
watch -n 0.5 cat results/wifi_online/live_wifi_rx_state.json
```

---

## 5. Quick start: 5G online sensing

Activate the environment:

```bash
cd DT_sensing_fusion_WIFI5G
source .venv_uhd/bin/activate
```

Short local test without SCP:

```bash
python src/python/ssb_python/online_5g_python_cfo_json_scp.py \
  --serial <rx_usrp_serial> \
  --freq 3541.44e6 \
  --rate 15.36e6 \
  --gain 60 \
  --duration-ms 20 \
  --num-iters 30 \
  --warmup-iters 5 \
  --channel 0 \
  --force-nid2 0 \
  --enable-cfo-correction \
  --cfo-warmup-iters 30 \
  --cfo-correction-sign -1 \
  --inference-backend torch \
  --torch-model results/binary_empty_vs_P5_rx/model_rxGridSSB/model.pt \
  --torch-device cpu \
  --mitsuba-position-map-json config/sionna_mitsuba_position_map.json \
  --enable-mitsuba-export \
  --disable-scp \
  --progress-every 1
```

Local outputs:

```text
results/online/live_inference_state_5G.json
results/online/live_person_sionna_scene.xml
```

---

## 6. Main scripts overview

| Area | Script | Purpose |
|---|---|---|
| 5G | `src/python/ssb_python/online_5g_python_cfo_json_scp.py` | Full Python 5G online inference, JSON/XML output, optional SCP |
| 5G | `src/python/ssb_python/rxgrid_torch_inference.py` | Loads the 5G PyTorch checkpoint and runs inference |
| 5G | `src/python/ssb_python/collect_labeled_rxgridssb_dataset_cfo.py` | Operator-friendly labeled 5G dataset collection |
| 5G | `src/python/ssb_python/analyze_rxgrid_distributions.py` | Dataset amplitude/phase analysis |
| WiFi | `src/python/wifi_sensing/tx_wifi_beacon_usrp.py` | USRP WiFi beacon transmitter |
| WiFi | `src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py` | Online WiFi beacon CSI receiver |
| WiFi | `src/python/wifi_sensing/rx_wifi_raw_capture_usrp.py` | Raw IQ capture for debugging/dataset fallback |
| WiFi | `src/python/wifi_sensing/process_wifi_raw_capture_ltf_tracker.py` | Offline robust WiFi CSI extraction from raw IQ |
| WiFi | `src/python/wifi_sensing/wifi_ltf_tracker.py` | L-LTF timing/tracking utilities |
| WiFi | `src/python/wifi_sensing/wifi_csi.py` | WiFi CSI extraction utilities |
| WiFi | `src/python/wifi_sensing/wifi_beacon_mac.py` | Beacon MAC MPDU generation |
| WiFi | `src/python/wifi_sensing/wifi_legacy_ofdm.py` | Legacy OFDM PHY waveform generation |

---

## 7. Dataset outputs

### 7.1 5G dataset output

```text
data/python_ssb_datasets/<label>/<session_id>/
  session_data.h5
  metadata.json
  capture_log.csv
```

Main H5 arrays:

```text
dataSSB   complex64, shape [360, 6, N]
rxGridSSB complex64, shape [240, 4, N]
```

### 7.2 WiFi dataset output

```text
data/wifi_csi_datasets/<label>/<session_id>/
  session_data.h5
  metadata.json
  capture_log.csv
```

Main H5 arrays:

```text
csi       complex64, shape [N, 52]
csi_amp   float32,   shape [N, 52]
csi_phase float32,   shape [N, 52]
ltf_metric
cfo_hz
rx_power_db
timestamp_unix
timestamp_usrp_rx
```

---

## 8. Model and inference status

### 8.1 5G

The 5G online script supports:

```text
--inference-backend threshold
--inference-backend torch
```

For real sensing, use the PyTorch backend with a trained checkpoint.

### 8.2 WiFi

The WiFi online RX currently supports:

```text
--inference-backend none
--inference-backend threshold
```

The threshold backend is a placeholder that writes label/confidence fields into the JSON. It is useful for testing the Digital Twin interface before training a real WiFi model.

Default placeholder inference:

```bash
python src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py \
  --serial "<rx_usrp_serial>,num_recv_frames=512,recv_frame_size=8200" \
  --inference-backend threshold
```

Future model workflow:

```text
1. Collect labeled WiFi CSI sessions.
2. Train a model on csi_amp/csi_phase or complex CSI features.
3. Add a torch backend for WiFi inference.
4. Run RX with --model-path path/to/model.pt.
```

---

## 9. Documentation map

| Goal | Read this |
|---|---|
| Overall repository overview | `README.md` |
| 5G deployment from a new computer | `README_5G_SSB_PYTHON_DEPLOYMENT.md` |
| 5G Python-only online notes | `README_online_python_5g.md` |
| WiFi quick start and command reference | `README_WIFI_BEACON_CSI.md` |
| WiFi technical pipeline | `docs/WIFI_BEACON_CSI_PIPELINE.md` |
| Legacy MATLAB/TCP 5G notes | `README_online_ssB_empty_p5.md` |
| Python environment notes | `requirements/README.md` |

---

## 10. Git policy

Do not commit generated datasets or runtime outputs:

```text
data/python_ssb_datasets/
data/wifi_csi_datasets/
results/online/
results/wifi_debug/
results/wifi_online/
logs/
__pycache__/
*.pyc
```

Commit code, configuration files, trained demo checkpoints if intentionally included, and documentation only.
