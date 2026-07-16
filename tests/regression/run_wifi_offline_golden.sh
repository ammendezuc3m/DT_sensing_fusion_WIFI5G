#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/../.."
    pwd
)"

cd "$ROOT_DIR"

CONFIG="configs/pipelines/wifi_beacon_offline.json"
LOG="results/runtime/offline_waveform_pipeline_v2.log"
FEATURES="results/runtime/wifi_beacon_features_v2.jsonl"

cmake --build build/cpp --parallel

rm -f "$LOG" "$FEATURES"

./build/cpp/offline_waveform_pipeline \
    --config "$CONFIG" \
    | tee "$LOG"

python3 \
    tests/regression/check_wifi_offline_pipeline.py \
    --log "$LOG" \
    --features "$FEATURES"
