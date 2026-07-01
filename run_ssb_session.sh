#!/usr/bin/env bash
set -e

LABEL="${1:?Uso: ./run_ssb_session.sh LABEL STATE DURATION PERSON ORIENTATION PAUSE}"
STATE="${2:-static}"
DURATION="${3:-120}"
PERSON="${4:-person01}"
ORIENTATION="${5:-unknown}"
PAUSE="${6:-20}"

cd "$HOME/AlbertoDir/DT_sensing_fusion"

echo "========================================"
echo "5G SSB Dataset Session"
echo "LABEL:       $LABEL"
echo "STATE:       $STATE"
echo "DURATION:    $DURATION s"
echo "PERSON:      $PERSON"
echo "ORIENTATION: $ORIENTATION"
echo "PAUSE:       $PAUSE s"
echo "========================================"

matlab -batch "addpath('src/matlab'); collect_ssb_dataset('${LABEL}','${STATE}',${DURATION},'${PERSON}','${ORIENTATION}',${PAUSE})"

