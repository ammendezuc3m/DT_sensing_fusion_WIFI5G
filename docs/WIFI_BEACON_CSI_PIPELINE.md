# WiFi Beacon CSI Pipeline

This document describes the WiFi sensing pipeline at implementation level.

The goal is to obtain one CSI sample per transmitted WiFi beacon and use those CSI samples later for classification/regression models.

---

## 1. System architecture

```text
PC2 / USRP B210 TX
  -> build Beacon MAC MPDU
  -> modulate as 802.11a/g legacy OFDM PPDU
  -> timed USRP TX every 100 ms

PC1 / USRP B210 RX
  -> continuous UHD RX at 20 Msps
  -> L-LTF seed detection
  -> beacon phase estimation
  -> periodic beacon tracking
  -> L-LTF CSI extraction
  -> H5/CSV/metadata write
  -> live JSON write
```

The design intentionally keeps TX and RX independent. Later, a 5G receiver can trigger or synchronize WiFi transmission/measurement logic, but the current implementation is already useful for WiFi-only dataset collection.

---

## 2. TX pipeline

Main script:

```text
src/python/wifi_sensing/tx_wifi_beacon_usrp.py
```

Supporting modules:

```text
src/python/wifi_sensing/wifi_beacon_mac.py
src/python/wifi_sensing/wifi_legacy_ofdm.py
src/python/wifi_sensing/wifi_live_json.py
```

TX steps:

```text
1. Generate Beacon MAC MPDU.
2. Add FCS.
3. Scramble, code, interleave and map using legacy OFDM 6 Mb/s BPSK 1/2.
4. Generate L-STF, L-LTF, L-SIG and DATA OFDM symbols.
5. Schedule one PPDU every 100 ms with UHD timed TX.
6. Write live TX state JSON.
```

Current PPDU properties:

```text
sample rate: 20 Msps
samples per beacon PPDU: 4160
airtime: 208 us
physical period: 100 ms
beacon interval field: 98 TU = 100.352 ms
```

Minimal command:

```bash
python src/python/wifi_sensing/tx_wifi_beacon_usrp.py --serial <tx_usrp_serial>
```

---

## 3. RX online pipeline

Main script:

```text
src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py
```

Supporting modules:

```text
src/python/wifi_sensing/wifi_ltf_tracker.py
src/python/wifi_sensing/wifi_csi.py
src/python/wifi_sensing/wifi_dataset_io.py
src/python/wifi_sensing/wifi_live_json.py
```

RX online steps:

```text
1. A background RX thread continuously receives IQ from UHD.
2. The main thread collects an initialization buffer.
3. L-LTF seeds are detected in the initialization buffer.
4. The beacon phase modulo 100 ms is estimated.
5. Future beacons are expected at phase + n * period.
6. The receiver searches only around each expected beacon.
7. Packet start is refined using L-LTF repeated-symbol correlation.
8. CSI is extracted from the two L-LTF long symbols.
9. One CSI vector of length 52 is saved per accepted beacon.
10. A live JSON state is updated for monitoring/integration.
```

Minimal command:

```bash
python src/python/wifi_sensing/rx_wifi_beacon_csi_online_usrp.py \
  --serial "<rx_usrp_serial>,num_recv_frames=512,recv_frame_size=8200"
```

---

## 4. L-LTF detector and tracker

The legacy WiFi preamble structure is:

```text
L-STF: 160 samples
L-LTF: 160 samples
  CP: 32 samples
  long symbol 1: 64 samples
  long symbol 2: 64 samples
```

The implemented timing metric compares the two repeated L-LTF long symbols:

```text
metric[p] = |sum conj(s1) * s2|^2 / (energy(s1) * energy(s2))
```

where `p` is a candidate packet start.

This method is more robust than correlating against a complete ideal preamble because the wireless channel, phase rotation, CFO, and multipath distort the absolute waveform. The repeated-symbol metric remains strong when the channel is approximately constant across the two L-LTF symbols.

---

## 5. CSI extraction

Main function:

```text
extract_csi_from_packet(...)
```

in:

```text
src/python/wifi_sensing/wifi_csi.py
```

CSI extraction steps:

```text
1. Use the two L-LTF symbols.
2. Estimate CFO from the phase difference between repeated long symbols.
3. Apply CFO correction.
4. FFT both long symbols.
5. Average the two frequency-domain estimates.
6. Divide by the known L-LTF sequence.
7. Keep the 52 active subcarriers.
```

Output:

```text
csi: complex64 array with shape [52]
```

Associated diagnostics:

```text
ltf_metric
cfo_hz
rx_power_db
timing_offset_samples
timestamp_unix
timestamp_usrp_rx
```

---

## 6. Online performance target

Beacon TX period:

```text
100 ms = 10 beacons/s
```

Therefore the ideal CSI rate is:

```text
10 CSI/s
```

A good online run is expected to reach approximately:

```text
9-10 CSI/s
0 RX overflows
period mean close to 0.100 s
```

Example tested result:

```text
60 s run
565 CSI packets
0 overflows
period close to 100 ms
```

---

## 7. Dataset format

Online RX output:

```text
data/wifi_csi_datasets/<label>/<session_id>/
  session_data.h5
  metadata.json
  capture_log.csv
```

H5 datasets:

```text
csi
csi_amp
csi_phase
timestamp_unix
timestamp_usrp_rx
packet_index
timing_offset_samples
cfo_hz
ltf_metric
rx_power_db
```

Expected shapes:

```text
csi       [N, 52] complex64
csi_amp   [N, 52] float32
csi_phase [N, 52] float32
```

---

## 8. Live JSON format

Live JSON path:

```text
results/wifi_online/live_wifi_rx_state.json
```

Important fields:

```text
schema_version
role
status
valid
session_id
packets_detected
missed_beacons
phase_samples
period_samples
last_packet
rx_stats
queue_size
elapsed_sec
```

The `last_packet` object contains:

```text
packet_index
timestamp_unix
timestamp_usrp_rx
global_offset_samples
expected_offset_samples
timing_error_samples
ltf_metric
cfo_hz
rx_power_db
csi_shape
inference
```

---

## 9. Offline debugging mode

When online performance needs debugging, use raw IQ capture and offline processing.

Raw capture:

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

Offline L-LTF tracking:

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

A clean 5 s capture should produce about 50 detections.

---

## 10. Current limitations

The current WiFi pipeline is designed for CSI sensing experiments, not normal WiFi connectivity.

Current limitations:

```text
No association or authentication workflow
No IP stack
No DHCP
No CSMA/CA
No hostapd
No MAC decode/filter validation in RX
No trained WiFi ML model yet
```

The RX currently assumes a controlled channel where the relevant repeated L-LTF bursts are from the experiment TX. The SSID/BSSID fields are stored as metadata and used to describe the session, not as decoded packet filters.

---

## 11. Future work

Planned next steps:

```text
1. Collect labeled WiFi CSI datasets.
2. Train a WiFi CSI model.
3. Add a torch backend to rx_wifi_beacon_csi_online_usrp.py.
4. Add optional MAC decode/filtering if multiple WiFi-like transmitters are present.
5. Integrate WiFi CSI output with the 5G online JSON/Digital Twin interface.
6. Add future 5G-triggered WiFi measurement scheduling.
```
