#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$HOME/AlbertoDir/DT_sensing_fusion"
cd "$PROJECT_DIR"

LABEL="${1:-empty}"
MOVEMENT_STATE="${2:-static}"
DURATION_SECONDS="${3:-30}"
PERSON_ID="${4:-none}"
ORIENTATION="${5:-none}"
PAUSE_BEFORE_SEC="${6:-10}"

PY_GAIN="${PY_GAIN:-60}"
PY_RATE="${PY_RATE:-15.36e6}"
PY_FREQ="${PY_FREQ:-3541.44e6}"
PY_DURATION_MS="${PY_DURATION_MS:-20}"
PY_WARMUP_ITERS="${PY_WARMUP_ITERS:-10}"
PY_CFO_WARMUP_ITERS="${PY_CFO_WARMUP_ITERS:-30}"
PY_PROGRESS_EVERY="${PY_PROGRESS_EVERY:-10}"
PY_POST="${PY_POST:-1}"

# Approximate Python iterations for the requested MATLAB duration.
# Python runs around 20 rxGridSSB/s, so duration*20 is a reasonable match.
PY_NUM_ITERS="${PY_NUM_ITERS:-$((DURATION_SECONDS * 20))}"

TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="logs/python_matlab_interleaved_${LABEL}_${TS}"
mkdir -p "$LOG_DIR"

echo "=== Interleaved Python/MATLAB capture ==="
echo "Project:           $PROJECT_DIR"
echo "Label:             $LABEL"
echo "Movement state:    $MOVEMENT_STATE"
echo "Duration MATLAB:   $DURATION_SECONDS s"
echo "Python iterations: $PY_NUM_ITERS"
echo "Python gain:       $PY_GAIN"
echo "Python post run:   $PY_POST"
echo "Log dir:           $LOG_DIR"
echo

run_python_capture () {
  local tag="$1"

  echo
  echo "=== Python CFO capture: $tag ==="

  source .venv_uhd/bin/activate

  python src/python/ssb_python/capture_online_rxgridssb_dataset_cfo.py \
    --serial 34B73C3 \
    --freq "$PY_FREQ" \
    --rate "$PY_RATE" \
    --gain "$PY_GAIN" \
    --duration-ms "$PY_DURATION_MS" \
    --num-iters "$PY_NUM_ITERS" \
    --warmup-iters "$PY_WARMUP_ITERS" \
    --channel 0 \
    --force-nid2 0 \
    --enable-cfo-correction \
    --cfo-warmup-iters "$PY_CFO_WARMUP_ITERS" \
    --cfo-correction-sign -1 \
    --progress-every "$PY_PROGRESS_EVERY" \
    --prefix "python_${LABEL}_${MOVEMENT_STATE}_${tag}_${TS}" \
    2>&1 | tee "$LOG_DIR/python_${tag}.log"
}

run_matlab_capture () {
  echo
  echo "=== MATLAB collect_ssb_dataset ==="

  matlab -batch "addpath('src/matlab'); collect_ssb_dataset('${LABEL}','${MOVEMENT_STATE}',${DURATION_SECONDS},'${PERSON_ID}','${ORIENTATION}',${PAUSE_BEFORE_SEC})" \
    2>&1 | tee "$LOG_DIR/matlab_collect.log"
}

echo "Latest MATLAB files before:"
find . -iname "*.mat" -printf "%T+ %p\n" | sort | tail -n 10 || true

run_python_capture "pre"
run_matlab_capture

if [[ "$PY_POST" == "1" ]]; then
  run_python_capture "post"
fi

echo
echo "=== Latest generated Python H5 files ==="
find results/python_online_rxgridssb_dataset_cfo -iname "*.h5" -printf "%T+ %p\n" | sort | tail -n 10 || true

echo
echo "=== Latest MATLAB MAT files ==="
find . -iname "*.mat" -printf "%T+ %p\n" | sort | tail -n 20 || true

echo
echo "Done."
echo "Logs saved in: $LOG_DIR"
