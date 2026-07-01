# DT_sensing_fusion

Passive 5G SSB sensing project using a USRP B210 receiver and a 5G Ericsson DOT/cell.

This repository contains the code needed to run **Python-only online 5G sensing**, collect labeled SSB datasets, and deploy a PyTorch model that writes JSON outputs for a Digital Twin or any other external consumer.

The previous MATLAB workflow is kept only as historical/reference material. The recommended deployment path is now Python-only.

---

## 1. Current recommended pipeline

The current online sensing chain is:

```text
USRP B210
  -> Python UHD IQ capture
  -> CFO warmup/correction
  -> PSS/NID2/timing detection
  -> OFDM demodulation
  -> dataSSB / rxGridSSB extraction
  -> PyTorch model inference
  -> local JSON
  -> optional SCP to a remote Digital Twin machine
```

Main online script:

```text
src/python/ssb_python/online_5g_python_cfo_json_scp.py
```

Current PyTorch model loader:

```text
src/python/ssb_python/rxgrid_torch_inference.py
```

Current demo checkpoint:

```text
results/binary_empty_vs_P5_rx/model_rxGridSSB/model.pt
```

Current dataset collection script:

```text
src/python/ssb_python/collect_labeled_rxgridssb_dataset_cfo.py
```

---

## 2. What the current model does

The included model is a **binary demonstration model** trained for the current lab setup:

```text
empty vs P5
```

Meaning:

```text
empty = no target/person in the trained scene
P5    = target/person at position P5 in the trained scene
```

Important: this model is **not a general human detector**.

It is not expected to generalize automatically to:

```text
a different factory
a different antenna placement
a different USRP/receiver position
a different room or geometry
a different set of target positions
a different 5G source/cell configuration
a different class set
```

For a new deployment, the correct workflow is:

```text
1. Install the environment.
2. Connect and test the USRP B210.
3. Collect a new labeled dataset in the target environment.
4. Train a new model using that dataset.
5. Replace the checkpoint used by the online script.
6. Run online inference with the new checkpoint.
```

The included checkpoint is mainly useful to demonstrate that the full end-to-end pipeline works.

---

## 3. Which document should I read?

### I want to deploy online 5G sensing on a new computer

Read:

```text
README_5G_SSB_PYTHON_DEPLOYMENT.md
```

This is the main deployment guide. It explains:

```text
hardware setup
fresh computer setup
UHD/Python environment
how to run online inference
how to send JSON by SCP
how to change the remote IP/path
how to collect a labeled dataset
how the dataset is stored
how to replace the model
```

---

### I want to run the current Python-only online inference

Read:

```text
README_online_python_5g.md
```

Main script:

```text
src/python/ssb_python/online_5g_python_cfo_json_scp.py
```

Typical local test:

```bash
cd ~/AlbertoDir/DT_sensing_fusion
source .venv_uhd/bin/activate

python src/python/ssb_python/online_5g_python_cfo_json_scp.py \
  --serial 34B73C3 \
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
  --disable-scp \
  --progress-every 1
```

Local JSON output:

```text
results/online/live_inference_state_5G.json
```

---

### I want to send the online JSON to another machine

Read:

```text
README_5G_SSB_PYTHON_DEPLOYMENT.md
```

Use the `--remote-target` argument of:

```text
src/python/ssb_python/online_5g_python_cfo_json_scp.py
```

Example:

```bash
--remote-target "factoryuser@192.168.1.50:/home/factoryuser/dt/live_inference_state_5G.json"
```

The current demo target is:

```text
nextnet@163.117.140.146:~/AlbertoDir/demo_5G/5G_inference/live_inference_state_5G.json
```

If SCP is too slow, send less frequently:

```bash
--scp-every 3
```

or:

```bash
--scp-every 5
```

---

### I want to collect a new labeled dataset

Read:

```text
README_5G_SSB_PYTHON_DEPLOYMENT.md
docs/DATASET_MODEL_JSON_GUIDE.md
```

Main script:

```text
src/python/ssb_python/collect_labeled_rxgridssb_dataset_cfo.py
```

Example for an empty scene:

```bash
cd ~/AlbertoDir/DT_sensing_fusion
source .venv_uhd/bin/activate

python src/python/ssb_python/collect_labeled_rxgridssb_dataset_cfo.py \
  --label empty \
  --scene static \
  --person-id none \
  --orientation none \
  --prep-sec 10 \
  --duration-sec 30 \
  --serial 34B73C3 \
  --freq 3541.44e6 \
  --rate 15.36e6 \
  --gain 60 \
  --duration-ms 20 \
  --channel 0 \
  --force-nid2 0 \
  --enable-cfo-correction \
  --cfo-warmup-iters 30 \
  --cfo-correction-sign -1 \
  --output-root data/python_ssb_datasets
```

Example for a target at `P5`:

```bash
python src/python/ssb_python/collect_labeled_rxgridssb_dataset_cfo.py \
  --label P5 \
  --scene static \
  --person-id person_1 \
  --orientation sideways \
  --prep-sec 10 \
  --duration-sec 30 \
  --serial 34B73C3 \
  --freq 3541.44e6 \
  --rate 15.36e6 \
  --gain 60 \
  --duration-ms 20 \
  --channel 0 \
  --force-nid2 0 \
  --enable-cfo-correction \
  --cfo-warmup-iters 30 \
  --cfo-correction-sign -1 \
  --output-root data/python_ssb_datasets
```

The script gives a preparation countdown before collection starts, so the operator can move to the target position.

Dataset output folder:

```text
data/python_ssb_datasets/<label>/<session_id>/
```

Each session contains:

```text
session_data.h5
metadata.json
capture_log.csv
```

---

### I want to understand the dataset format

Read:

```text
docs/DATASET_MODEL_JSON_GUIDE.md
```

The key arrays are:

```text
dataSSB   = 360 x 6 x N
rxGridSSB = 240 x 4 x N
```

The online model uses `rxGridSSB`.

For one sample:

```text
rxGridSSB = 240 subcarriers x 4 OFDM symbols
```

For the current PyTorch model, this complex grid is converted to:

```text
[2, 240, 4]
channel 0 = abs(rxGridSSB)
channel 1 = angle(rxGridSSB)
```

---

### I want to test a model on a saved dataset

Read:

```text
docs/DATASET_MODEL_JSON_GUIDE.md
```

Main script:

```text
src/python/ssb_python/test_rxgrid_torch_checkpoint_on_h5.py
```

Example:

```bash
LAST_H5="$(find data/python_ssb_datasets -name session_data.h5 | sort | tail -n 1)"

python src/python/ssb_python/test_rxgrid_torch_checkpoint_on_h5.py \
  --model-pt results/binary_empty_vs_P5_rx/model_rxGridSSB/model.pt \
  --input-h5 "$LAST_H5" \
  --max-samples 30
```

---

### I want to replace the model

Read:

```text
README_5G_SSB_PYTHON_DEPLOYMENT.md
docs/DATASET_MODEL_JSON_GUIDE.md
```

The online script accepts another checkpoint with:

```bash
--torch-model path/to/new/model.pt
```

Example:

```bash
python src/python/ssb_python/online_5g_python_cfo_json_scp.py \
  ... \
  --inference-backend torch \
  --torch-model results/my_new_model/model.pt
```

The current loader expects a checkpoint containing:

```text
model_state_dict
mean
std
input_shape = [2, 240, 4]
complex_mode = abs_phase
classes = [...]
```

If the new model uses the same architecture and input format, no code change is needed.

If the new model uses a different architecture or different input channels, update:

```text
src/python/ssb_python/rxgrid_torch_inference.py
```

---

### I want to inspect whether a captured dataset looks stable

Read:

```text
docs/DATASET_MODEL_JSON_GUIDE.md
```

Main script:

```text
src/python/ssb_python/analyze_rxgrid_distributions.py
```

Example:

```bash
LAST_H5="$(find data/python_ssb_datasets -name session_data.h5 | sort | tail -n 1)"

python src/python/ssb_python/analyze_rxgrid_distributions.py \
  --input "$LAST_H5" \
  --dataset rxGridSSB \
  --label python_dataset
```

Useful plots include:

```text
amplitude histogram
phase histogram
IQ scatter
mean amplitude heatmap
mean amplitude by subcarrier
mean amplitude by OFDM symbol
```

---

### I want to compare with the old MATLAB reference

This is not needed for normal deployment.

For historical validation and comparison only, see:

```text
run_python_matlab_interleaved_capture.sh
src/python/ssb_python/compare_interleaved_python_matlab.py
```

MATLAB was used during development to verify that the Python SSB extraction produced a consistent `rxGridSSB` representation. The current practical deployment path does not require MATLAB.

---

### I want the old MATLAB/TCP online notes

Read:

```text
README_online_ssB_empty_p5.md
```

This is legacy documentation. It describes the previous online flow:

```text
MATLAB capture
MATLAB rxGridSSB extraction
TCP to Python
Python inference
JSON/SCP
```

This flow is kept for reference, but it is no longer the recommended deployment path.

---

## 4. Main scripts overview

| Script | Purpose |
|---|---|
| `src/python/ssb_python/online_5g_python_cfo_json_scp.py` | Full Python online inference, JSON output, optional SCP |
| `src/python/ssb_python/rxgrid_torch_inference.py` | Loads the PyTorch `.pt` checkpoint and runs inference |
| `src/python/ssb_python/collect_labeled_rxgridssb_dataset_cfo.py` | Operator-friendly labeled dataset collection |
| `src/python/ssb_python/capture_online_rxgridssb_dataset_cfo.py` | Lower-level CFO-corrected online dataset capture |
| `src/python/ssb_python/test_rxgrid_torch_checkpoint_on_h5.py` | Tests a model checkpoint on a saved H5 dataset |
| `src/python/ssb_python/analyze_rxgrid_distributions.py` | Plots amplitude/phase statistics for saved datasets |
| `src/python/ssb_python/capture_iq_blocks_uhd.py` | Raw IQ capture for low-level debugging |
| `src/python/ssb_python/compare_interleaved_python_matlab.py` | Historical Python/MATLAB comparison tool |

---

## 5. Recommended new-factory workflow

For a new factory or deployment scenario:

```text
1. Clone the repository on the target computer.
2. Install UHD and create the Python UHD environment.
3. Connect the USRP B210 and verify that UHD detects it.
4. Adjust USRP parameters if needed:
   frequency, sample rate, gain, channel, NID2.
5. Decide the target classes:
   empty, P1, P2, P3, person, no_person, etc.
6. Collect labeled datasets with collect_labeled_rxgridssb_dataset_cfo.py.
7. Inspect the datasets with analyze_rxgrid_distributions.py.
8. Train a new model using the collected Python rxGridSSB datasets.
9. Save the model checkpoint in the expected format.
10. Run online_5g_python_cfo_json_scp.py with --torch-model pointing to the new checkpoint.
11. Set --remote-target to the desired Digital Twin or external machine.
12. Validate that the receiver of the JSON reads the expected fields.
```

---

## 6. Git policy

Do not commit generated datasets or runtime outputs:

```text
data/python_ssb_datasets/
results/online/
results/python_online_rxgridssb_dataset_cfo/
results/python_rxgrid_distribution/
logs/
```

Commit code, configurations, documentation, and small demonstration assets only.

The current small demo model checkpoint is kept in the repository for demonstration:

```text
results/binary_empty_vs_P5_rx/model_rxGridSSB/model.pt
```

---

## 7. Quick links

| Goal | Read this |
|---|---|
| Deploy from a new computer | `README_5G_SSB_PYTHON_DEPLOYMENT.md` |
| Run Python-only online inference | `README_online_python_5g.md` |
| Understand dataset/model/JSON format | `docs/DATASET_MODEL_JSON_GUIDE.md` |
| Run step-by-step commands | `docs/RUNBOOK_PYTHON_ONLINE_5G.md` |
| Historical MATLAB/TCP notes | `README_online_ssB_empty_p5.md` |
