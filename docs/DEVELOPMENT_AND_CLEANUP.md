# Desarrollo y limpieza segura

## Conservar

```text
apps/waveform_rx/online_waveform_pipeline.cpp
apps/waveform_rx/offline_waveform_pipeline.cpp
apps/waveform_rx/offline_wifi_decode.cpp

src/cpp/common/
src/cpp/io/
src/cpp/pipeline/
src/cpp/publishers/
src/cpp/sources/
src/cpp/waveforms/wifi_nonht/

configs/pipelines/wifi_beacon_offline.json
configs/pipelines/wifi_beacon_online.json

tests/golden/
tests/regression/check_wifi_offline_pipeline.py

tools/receivers/zeromq_feature_receiver.py
```

`offline_wifi_decode.cpp` se conserva temporalmente como referencia.

## Añadir a `.gitignore`

```gitignore
build/
results/runtime/
results/csi/live/
results/csi/sessions/
*.log

.venv/
.venv_uhd/
__pycache__/
*.pyc

.vscode/
.idea/
```

No ignorar `tests/golden/` si contiene referencias necesarias.

## Auditar antes de borrar

```bash
git status --short

find apps src tools configs tests   -type f | sort > /tmp/repo_inventory.txt

git ls-files | sort > /tmp/tracked_files.txt
```

Buscar referencias antes de eliminar:

```bash
grep -R -n   --exclude-dir=.git   --exclude-dir=build   "NOMBRE_DEL_ARCHIVO_O_SIMBOLO" .
```

## Limpieza recomendada

Mover primero prototipos dudosos:

```bash
mkdir -p legacy/prototypes
git mv ruta/script_antiguo.py legacy/prototypes/
```

Después compilar y probar. Solo eliminar definitivamente en un commit posterior.

## Validación obligatoria

```bash
cmake -S src/cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/cpp --parallel

python3 tests/regression/check_wifi_offline_pipeline.py

./build/cpp/online_waveform_pipeline   --config configs/pipelines/wifi_beacon_online.json
```

Comprobar:

```text
frames > 0
local_written = frames
CSI = 52
overflows = 0
```

Validar JSONL:

```bash
jq -e . results/csi/live/latest.jsonl >/dev/null
wc -l results/csi/live/latest.jsonl
```

## Commits recomendados

```bash
git add apps/waveform_rx src/cpp configs/pipelines tests
git commit -m "feat: add real-time UHD WiFi CSI pipeline"
```

```bash
git add README.md docs .gitignore   apps/waveform_rx/online_waveform_pipeline.cpp   src/cpp/io configs/pipelines/wifi_beacon_online.json

git commit -m "feat: record real-time CSI to local JSONL"
```

```bash
git add -A
git commit -m "chore: archive obsolete sensing prototypes"
```

## Push

```bash
git status
git log --oneline --decorate -5
git diff --cached
git push -u origin HEAD
```

No ejecutar `git clean -fd`, `rm -rf` ni borrados masivos antes de tener una rama, revisar `git status` y pasar compilación y regresión.
