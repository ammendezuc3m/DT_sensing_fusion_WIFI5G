# DT_sensing_fusion

Passive 5G SSB sensing project using a USRP B210 receiver and a 5G Ericsson DOT/cell.

The current pipeline supports:

- MATLAB-based SSB capture.
- Extraction of `dataSSB`.
- Online extraction of `rxGridSSB`.
- Python/PyTorch online inference.
- JSON export.
- SCP export to a remote machine for Digital Twin integration.
- Initial preparation for a Python-only UHD/SSB implementation.

---

## 1. Main project path

```bash
cd ~/AlbertoDir/DT_sensing_fusion
```

---

## 2. Current online EMPTY/P5 inference

The current binary model distinguishes:

```text
EMPTY = no person
P5    = person at position P5
```

Current online pipeline:

```text
MATLAB captures one valid SSB
MATLAB extracts rxGridSSB = dataSSB(61:300, 2:5)
MATLAB sends rxGridSSB to Python by TCP
Python runs PyTorch inference
Python applies temporal stabilization
Python writes a local JSON
Python sends the JSON to the remote Digital Twin machine by SCP
```

Run online inference:

```bash
cd ~/AlbertoDir/DT_sensing_fusion

./run_online_5g_binary_json_scp.sh
```

Short test:

```bash
cd ~/AlbertoDir/DT_sensing_fusion

MAX_VALID_SSB=200 ./run_online_5g_binary_json_scp.sh
```

---

## 3. Local and remote JSON

Local JSON:

```bash
results/online/live_inference_state_5G.json
```

Remote JSON:

```bash
nextnet@163.117.140.146:~/AlbertoDir/demo_5G/5G_inference/live_inference_state_5G.json
```

Check remote JSON once:

```bash
ssh nextnet@163.117.140.146 \
  "cat ~/AlbertoDir/demo_5G/5G_inference/live_inference_state_5G.json"
```

Watch remote JSON from the local machine:

```bash
watch -n 0.5 "ssh nextnet@163.117.140.146 'cat ~/AlbertoDir/demo_5G/5G_inference/live_inference_state_5G.json'"
```

---

## 4. Dataset capture command

Example:

```bash
matlab -batch "addpath('src/matlab'); collect_ssb_dataset('empty','static',30,'none','none',10)"
```

Meaning:

```text
label           = 'empty'
movementState   = 'static'
durationSeconds = 30
personId        = 'none'
orientation     = 'none'
pauseBeforeSec  = 10
```

General format:

```bash
matlab -batch "addpath('src/matlab'); collect_ssb_dataset('<label>','<movementState>',<durationSeconds>,'<personId>','<orientation>',<pauseBeforeSec>)"
```

Examples:

```bash
matlab -batch "addpath('src/matlab'); collect_ssb_dataset('empty','static',30,'none','none',10)"
matlab -batch "addpath('src/matlab'); collect_ssb_dataset('P5','static',30,'person_1','sideways',10)"
```

---

## 5. Dataset block launcher

```bash
./run_datassb_block.sh empty 1 none datassb_side_v1_6labels 10000
./run_datassb_block.sh P5 1 sideways datassb_side_v1_6labels 10000
```

---

## 6. Training binary model

```bash
cd ~/AlbertoDir/DT_sensing_fusion

.venv/bin/python src/python/train_datassb_binary_pipeline.py \
  --models rx \
  --out-dir results/binary_empty_vs_P5_rx \
  --skip-dataset-cache \
  --epochs 20
```

---

## 7. Manual online execution

Terminal 1: Python server.

```bash
cd ~/AlbertoDir/DT_sensing_fusion

.venv/bin/python src/python/online_rxgridssb_inference_server.py
```

Terminal 2: MATLAB streamer.

```bash
cd ~/AlbertoDir/DT_sensing_fusion

SEND_EVERY_N=1 \
matlab -batch "run('src/matlab/stream_rxgridssb_online_to_python.m')"
```

Short test:

```bash
cd ~/AlbertoDir/DT_sensing_fusion

MAX_VALID_SSB=100 \
SEND_EVERY_N=1 \
matlab -batch "run('src/matlab/stream_rxgridssb_online_to_python.m')"
```

---

## 8. UHD Python environment

Python UHD capture is developed separately from the inference environment.

Use:

```bash
source .venv_uhd/bin/activate
```

Check B210:

```bash
python - <<'PY'
import uhd

usrp = uhd.usrp.MultiUSRP("serial=34B73C3")
print("USRP OK")
print("RX channels:", usrp.get_rx_num_channels())
print("Motherboard:", usrp.get_mboard_name())
PY
```

---

## 9. Python UHD raw IQ capture

The first Python-only migration step is implemented.

Scripts:

```text
src/python/ssb_python/test_capture_iq_uhd.py
src/python/ssb_python/capture_iq_blocks_uhd.py
```

These scripts reproduce the raw SDR capture stage of the MATLAB pipeline:

```matlab
waveform = capture(rx, captureDuration);
```

### 9.1. Single 20 ms IQ capture

```bash
cd ~/AlbertoDir/DT_sensing_fusion
source .venv_uhd/bin/activate

python src/python/ssb_python/test_capture_iq_uhd.py \
  --serial 34B73C3 \
  --freq 3541.44e6 \
  --rate 15.36e6 \
  --gain 60 \
  --duration-ms 20 \
  --channel 0
```

Expected output:

```text
waveform shape = (307200,)
dtype = complex64
```

### 9.2. Repeated 20 ms IQ block capture

```bash
cd ~/AlbertoDir/DT_sensing_fusion
source .venv_uhd/bin/activate

python src/python/ssb_python/capture_iq_blocks_uhd.py \
  --serial 34B73C3 \
  --freq 3541.44e6 \
  --rate 15.36e6 \
  --gain 60 \
  --duration-ms 20 \
  --num-blocks 20 \
  --channel 0 \
  --progress-every 1
```

Expected output:

```text
waveform shape = (20, 307200)
dtype = complex64
```

The recommended initial gain for the Python port is:

```text
gain = 60 dB
```

Observed gain comparison during initial tests:

```text
gain=70:
  sat_real_gt_0p99_percent_mean ≈ 0.65 %
  sat_imag_gt_0p99_percent_mean ≈ 0.64 %

gain=65:
  sat_real_gt_0p99_percent_mean ≈ 0.57 %
  sat_imag_gt_0p99_percent_mean ≈ 0.56 %

gain=60:
  sat_real_gt_0p99_percent_mean ≈ 0.42 %
  sat_imag_gt_0p99_percent_mean ≈ 0.44 %
```

Captured IQ files are saved under `data/`, which is intentionally ignored by Git.

---

## 10. Files intentionally not tracked by Git

The repository does not track:

```text
.venv/
.venv_uhd/
.venv_datassb/
data/
logs/
runtime/
backups/
large .mat captures
large .h5 captures
temporary online JSON files
```

These files are local/generated artifacts and should be rebuilt or regenerated when needed.
