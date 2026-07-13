# WiFi Beacon CSI Sensing with USRP B210

This document describes the WiFi sensing branch of `DT_sensing_fusion_WIFI5G`.

The current implementation uses one USRP B210 as an active transmitter and a
second USRP B210 as a receiver. The transmitter sends deterministic
802.11 legacy OFDM beacon frames. The receiver will detect those frames,
identify that they belong to this experiment, extract L-LTF CSI and feed the
sanitized CSI to an online sensing model.

The 5G SSB pipeline is independent and is documented separately.

---

## 1. Architecture

```text
TX computer / USRP B210
  -> build an 802.11 legacy OFDM beacon MPDU
  -> add experiment-specific Vendor IE
  -> calculate FCS
  -> generate L-STF + L-LTF + L-SIG + DATA
  -> transmit timed bursts with UHD every 100 TU

RX computer / USRP B210
  -> continuous IQ reception at 20 Msps
  -> L-STF packet detection
  -> coarse CFO estimation and correction
  -> L-LTF timing alignment
  -> fine CFO correction
  -> 64-point OFDM FFT
  -> CSI estimation on 52 active L-LTF subcarriers
  -> L-SIG and DATA decoding
  -> BSSID + FCS + Vendor IE validation
  -> CSI sanitization
  -> online inference
```

The RX chain is the next development stage. The TX chain described below has
already been validated at the waveform, MAC, UHD streaming and timed-burst
levels.

---

## 2. Current TX format

The current beacon configuration is:

```text
PHY:                   802.11 legacy OFDM, 20 MHz
Rate:                  6 Mb/s, BPSK, coding rate 1/2
Sample rate:           20 Msps
Beacon interval:       100 TU
Physical period:       102.4 ms
Default SSID:          SENSING_WIFI
Default BSSID:         02:11:22:33:44:55
Default RF channel:    WiFi channel 1
Default RF frequency:  2412 MHz
```

One TU is 1024 microseconds, therefore:

```text
100 TU = 102.4 ms
```

The RF center frequency and the channel announced in the beacon must agree.

Common 2.4 GHz choices are:

| WiFi channel | Center frequency |
|---:|---:|
| 1 | 2412 MHz |
| 6 | 2437 MHz |
| 11 | 2462 MHz |

---

## 3. Experiment-specific Vendor IE

Each beacon contains a Vendor Specific Information Element before the FCS is
calculated.

Default fields:

```text
Element ID:       221
OUI:              02:11:22
Vendor type:      1
Magic:            ALBSENS
Version:          1
Transmitter ID:   1
Experiment ID:    1
Packet counter:   incremented for every beacon
```

The receiver must only accept CSI after validating at least:

```text
Beacon subtype
FCS
BSSID
Vendor OUI
Vendor magic
Transmitter ID
Experiment ID
Packet counter
```

L-STF and L-LTF alone cannot identify the transmitter because those fields are
shared by legacy OFDM packets from other WiFi devices.

---

## 4. Main source files

```text
src/python/wifi_sensing/tx_wifi_usrp.py
src/python/wifi_sensing/wifi_beacon_mac.py
src/python/wifi_sensing/wifi_legacy_ofdm.py
```

Responsibilities:

```text
tx_wifi_usrp.py
  Common packet-mode UHD transmitter.
  Current mode: --mode beacon
  Reserved future mode: --mode bf

wifi_beacon_mac.py
  Beacon MAC construction, tagged parameters, extra IEs and FCS.

wifi_legacy_ofdm.py
  Legacy OFDM PPDU generation:
  L-STF + L-LTF + L-SIG + DATA.
```

---

## 5. Environment

Ubuntu packages:

```bash
sudo apt update
sudo apt install -y uhd-host python3-uhd python3-venv
```

Create the UHD environment from the repository root:

```bash
python3 -m venv --system-site-packages .venv_uhd
source .venv_uhd/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/requirements-uhd.txt
```

Check UHD and the radio:

```bash
uhd_find_devices
```

Check the Python binding:

```bash
python - <<'PY'
import uhd
print("UHD Python binding OK")
PY
```

---

## 6. Offline TX validation

Run from the repository root:

```bash
source .venv_uhd/bin/activate

python -m src.python.wifi_sensing.tx_wifi_usrp \
  --mode beacon \
  --freq 2.412e9 \
  --wifi-channel 1 \
  --beacon-interval-tu 100 \
  --dry-run
```

This creates:

```text
results/wifi_debug/first_tx_packet.npz
results/wifi_online/live_wifi_tx_state.json
```

The generated NPZ contains:

```text
waveform
mpdu
sample_rate_hz
metadata_json
```

The dry run must report:

```text
period_ms: 102.4
sample rate: 20.000 Msps
first waveform samples: 4800
first waveform duration: 240 us
```

### Check Vendor IE and waveform

```bash
python - <<'PY'
import json
import numpy as np

data = np.load("results/wifi_debug/first_tx_packet.npz")
waveform = data["waveform"]
mpdu = data["mpdu"].tobytes()
metadata = json.loads(str(data["metadata_json"][0]))
vendor_ie = bytes.fromhex(metadata["vendor_ie_hex"])

print("Samples:", len(waveform))
print("dtype:", waveform.dtype)
print("Peak:", float(np.max(np.abs(waveform))))
print("MPDU bytes:", len(mpdu))
print("Vendor IE present:", vendor_ie in mpdu)
print(json.dumps(metadata, indent=2))
PY
```

Expected key result:

```text
Vendor IE present: True
```

### Check FCS

```bash
python - <<'PY'
import struct
import zlib
import numpy as np

mpdu = np.load(
    "results/wifi_debug/first_tx_packet.npz"
)["mpdu"].tobytes()

expected = struct.pack("<I", zlib.crc32(mpdu[:-4]) & 0xffffffff)

print("Stored FCS:    ", mpdu[-4:].hex())
print("Calculated FCS:", expected.hex())
print("FCS valid:", mpdu[-4:] == expected)
PY
```

Expected:

```text
FCS valid: True
```

---

## 7. RF transmission

First identify the serial number:

```bash
uhd_find_devices
```

Example command:

```bash
python -m src.python.wifi_sensing.tx_wifi_usrp \
  --mode beacon \
  --serial 34B73C3 \
  --freq 2.412e9 \
  --wifi-channel 1 \
  --rate 20e6 \
  --bandwidth 20e6 \
  --gain 10 \
  --antenna "TX/RX" \
  --ssid "SENSING_WIFI" \
  --bssid "02:11:22:33:44:55" \
  --beacon-interval-tu 100 \
  --vendor-oui "02:11:22" \
  --vendor-type 1 \
  --vendor-magic "ALBSENS" \
  --vendor-version 1 \
  --transmitter-id 1 \
  --experiment-id 1 \
  --num-packets 1000 \
  --progress-every 10
```

Use a moderate initial gain when TX and RX are close. Increase it only after
checking that the receiver is not saturated.

A successful UHD run should show:

```text
actual TX rate: 20.000000 Msps
actual TX frequency: 2412.000000 MHz
zero_sends=0
```

At the end:

```text
Sent packets: 1000
Total sent samples: 4800000
Total zero sends: 0
```

`burst_ack` means that UHD processed the corresponding timed burst. It does not
by itself measure RF power at the antenna connector. Independent reception with
the second USRP is still required to prove over-the-air propagation.

---

## 8. Validated status

Validated on the current TX machine:

```text
Beacon MAC construction
100 TU beacon interval
Vendor IE insertion before FCS
Valid FCS
20 Msps legacy OFDM waveform
4800 samples / 240 us per PPDU
UHD B210 configuration over USB 3
Timed TX
1000 packets submitted
4,800,000 samples submitted
0 zero-length sends
No observed UHD underflow, sequence or time errors
999 asynchronous burst acknowledgements observed
```

The missing final acknowledgement is not considered evidence of a failed
transmission; the asynchronous queue was drained during the packet loop and
the final event may arrive after the last poll.

Pending:

```text
Independent over-the-air RF reception
New L-STF detector
Coarse and fine CFO correction
L-LTF CSI extraction
L-SIG and DATA decoding
Vendor IE validation at RX
CSI sanitization
Online model inference
```

---

## 9. CSI sanitization plan

Before inference, the receiver pipeline will:

```text
Remove common phase rotation
Remove linear phase slope across subcarriers
Normalize amplitude
Reject low-quality packets
Evaluate subcarrier or antenna ratios
Start with CSI magnitude before adding phase
Keep TX and RX gains fixed during each experiment
```

The initial model input will prioritize `abs(H[k])`. Sanitized phase will only
be introduced after verifying that it contributes information without learning
clock, timing or hardware drift.

---

## 10. Future beamforming mode

The common transmitter supports packet builders by mode:

```bash
--mode beacon
--mode bf
```

`--mode bf` is reserved and intentionally not implemented yet.

A beamforming implementation may require more than a different MAC packet:

```text
Multiple TX chains
Relative phase calibration
Cable and antenna calibration
Complex beamforming weights
Synchronous multi-channel streaming
A defined sounding or feedback protocol
```

The current abstraction allows that extension without duplicating the complete
UHD control and timed-transmission logic.
