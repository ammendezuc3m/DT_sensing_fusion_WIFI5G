#!/usr/bin/env bash
set -euo pipefail

LABEL="${1:-}"
BLOCK="${2:-}"
ORIENTATION="${3:-sideways}"
DATASET_NAME="${4:-datassb_side_v1}"
CAPTURES_PER_BLOCK="${5:-10000}"

if [[ -z "$LABEL" || -z "$BLOCK" ]]; then
  echo "Usage:"
  echo "  ./run_datassb_block.sh <label> <block> [orientation] [dataset_name] [captures_per_block]"
  echo ""
  echo "Examples:"
  echo "  ./run_datassb_block.sh empty 1 none datassb_side_v1 10000"
  echo "  ./run_datassb_block.sh P1    1 sideways datassb_side_v1 10000"
  exit 1
fi

echo "=== dataSSB dataset block capture ==="
echo "Label:              $LABEL"
echo "Block:              $BLOCK"
echo "Orientation:        $ORIENTATION"
echo "Dataset name:       $DATASET_NAME"
echo "Captures per block: $CAPTURES_PER_BLOCK"
echo ""

CAPTURE_LABEL="$LABEL" \
CAPTURE_BLOCK="$BLOCK" \
CAPTURE_ORIENTATION="$ORIENTATION" \
DATASET_NAME="$DATASET_NAME" \
CAPTURES_PER_BLOCK="$CAPTURES_PER_BLOCK" \
matlab -batch "run('src/matlab/capture_datassb_dataset_block.m')"

