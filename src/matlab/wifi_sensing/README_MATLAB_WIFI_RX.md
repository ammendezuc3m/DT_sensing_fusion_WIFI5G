# MATLAB WiFi beacon RX

Copy the four `.m` files into `src/matlab/wifi_sensing/`.

Run capture and decode:

```bash
matlab -sd ~/AlbertoDir/DT_sensing_fusion_WIFI5G \
  -batch "addpath('src/matlab/wifi_sensing','-begin'); run_wifi_beacon_rx"
```
