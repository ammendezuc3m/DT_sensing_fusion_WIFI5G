# CSI local en tiempo real

## Flujo

```text
B210 → UHD → WiFi Non-HT → CSI de 52 subportadoras → JSONL local
```

El transmisor actual reproduce un buffer con temporización planificada. Sirve para validar recepción, decodificación, CSI y escritura local.

## Ejecución

```bash
cd ~/AlbertoDir/DT_sensing_fusion_WIFI5G

./build/cpp/online_waveform_pipeline   --config configs/pipelines/wifi_beacon_online.json
```

Después se inicia el transmisor. Para terminar, `Ctrl+C`.

## Fichero producido

```text
results/csi/live/latest.jsonl
```

Cada línea contiene:

```text
packet_counter
rx_timestamp_ns
snr_db
cfo_hz
power_dbfs
complex_features
```

`complex_features` tiene 52 elementos:

```json
[
  {"real": 0.012, "imag": -0.031},
  {"real": 0.018, "imag": -0.027}
]
```

## Ver solo datos nuevos

`tail` muestra por defecto diez líneas antiguas. Para ver únicamente lo que llegue desde ese momento:

```bash
tail -n 0 -F results/csi/live/latest.jsonl
```

CSI completo como matriz `52 × 2`:

```bash
tail -n 0 -F results/csi/live/latest.jsonl   | jq -c '.complex_features | map([.real, .imag])'
```

Contador, timestamp y CSI:

```bash
tail -n 0 -F results/csi/live/latest.jsonl   | jq -c '{
      counter: .packet_counter,
      timestamp_ns: .rx_timestamp_ns,
      csi: (.complex_features | map([.real, .imag]))
    }'
```

## Leer desde Python

```python
import json
import time
from pathlib import Path

path = Path("results/csi/live/latest.jsonl")

while not path.exists():
    time.sleep(0.1)

with path.open("r", encoding="utf-8") as file:
    file.seek(0, 2)

    while True:
        line = file.readline()

        if not line:
            time.sleep(0.01)
            continue

        frame = json.loads(line)
        csi = [
            complex(x["real"], x["imag"])
            for x in frame["complex_features"]
        ]

        print(frame["packet_counter"], len(csi))
```

## Frecuencia temporal

Con beacon interval de `100 TU`, el periodo nominal es `102,4 ms`. Puede faltar algún frame si no se detecta, sincroniza, decodifica o valida.

La escritura realiza `flush()` por frame, por lo que cada línea queda disponible inmediatamente.

## Indicadores correctos

```text
CSI=52
overflows=0
timeouts=0
local_written=frames
cola sin crecimiento sostenido
latencia < 102,4 ms
```

## Direct binary CSI output

In addition to the JSONL output, the receiver writes the complex CSI
vectors directly to:

```text
results/csi/live/latest_csi.cf32
Each validated beacon produces:

1 new line in latest.jsonl
52 new complex64 values in latest_csi.cf32

Each complex value is stored as:

float32 real
float32 imaginary

The binary file can be loaded directly as a matrix:

import numpy as np

csi = np.fromfile(
    "results/csi/live/latest_csi.cf32",
    dtype=np.complex64,
)

if csi.size % 52 != 0:
    raise RuntimeError(
        "The CSI file does not contain complete frames"
    )

csi = csi.reshape(-1, 52)

print("Shape:", csi.shape)
print("Last CSI:", csi[-1])

Row i in the binary file corresponds to line i in the JSONL file.

The correspondence can be validated with:

import json
import numpy as np

with open(
    "results/csi/live/latest.jsonl",
    encoding="utf-8",
) as file:
    metadata = [
        json.loads(line)
        for line in file
        if line.strip()
    ]

csi = np.fromfile(
    "results/csi/live/latest_csi.cf32",
    dtype=np.complex64,
).reshape(-1, 52)

print("JSONL frames:", len(metadata))
print("Raw CSI frames:", csi.shape[0])

assert len(metadata) == csi.shape[0]

The receiver writes one binary CSI frame only after the beacon has
passed packet detection, synchronization, PHY decoding, FCS validation,
BSSID filtering, and Vendor IE validation.
