# Python WLAN beacon receiver v2

Clean legacy 802.11 OFDM receiver pipeline:

1. L-STF autocorrelation detector.
2. Coarse CFO.
3. Multi-hypothesis L-LTF timing.
4. Fine CFO.
5. L-LTF channel estimate.
6. L-SIG decode.
7. DATA decode.
8. MPDU/FCS parse.
9. Beacon, SSID, BSSID and ALBSENS Vendor IE validation.
10. CSI accepted only after full identity validation.

## Copy

Copy `src/python/wifi_sensing/wlan_rx/` and the three top-level Python files into the repository preserving paths.

## Synthetic test

```bash
python -m src.python.wifi_sensing.test_wifi_receiver_v2
```

## Real capture

```bash
python -m src.python.wifi_sensing.decode_wifi_capture_v2 \
  --input results/wifi_rx/raw_wifi_capture_2s.npz \
  --stf-threshold 0.55 \
  --stf-min-plateau 24 \
  --min-separation-samples 4800 \
  --min-ltf-template-metric 0.05 \
  --max-ltf-consistency-error 0.40 \
  --verbose-rejects
```

## Single-burst diagnostics

Use an energy peak from the capture:

```bash
python -m src.python.wifi_sensing.analyze_wifi_burst_v2 \
  --input results/wifi_rx/raw_wifi_capture_2s.npz \
  --center-sample 18432200 \
  --radius 12000
```
