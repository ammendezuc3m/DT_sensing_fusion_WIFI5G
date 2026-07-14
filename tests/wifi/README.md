# WiFi legacy OFDM validation tests

Este directorio contiene las pruebas utilizadas para validar la generación,
transmisión y recepción de beacons IEEE 802.11 legacy OFDM empleados por el
subsistema WiFi sensing.

La implementación operativa está en:

- `src/python/wifi_sensing/`
- `src/matlab/wifi_sensing/`

Los tests se mantienen separados del código operativo.

## Estado validado

La waveform generada en Python se ha validado con MATLAB R2025b y WLAN
Toolbox:

- L-STF válido.
- L-LTF válido.
- Sincronización temporal válida.
- CFO estimado correctamente.
- L-SIG válido.
- MCS 0: BPSK, tasa 1/2, 6 Mb/s.
- PSDU de 161 bytes.
- DATA recuperado.
- MPDU y FCS válidos.
- Frame type `Beacon`.
- SSID `SENSING_WIFI`.
- BSSID `02:11:22:33:44:55`.
- Vendor IE `ALBSENS` válido.
- Contador de paquete válido.
- CSI L-LTF de 52 subportadoras.

## Errores encontrados

### Codificador convolucional

El registro K=7 introducía el bit nuevo en el extremo incorrecto.

Implementación correcta:

```python
state = ((state >> 1) | ((bit & 1) << 6)) & 0x7F
```

Polinomios:

- G0 = 133 octal.
- G1 = 171 octal.

El TX Python y el RX Python anterior compartían la misma convención
incorrecta, por lo que sus tests internos pasaban aunque la señal no fuera
compatible con receptores WLAN estándar.

### Polaridad BPSK

La convención validada contra WLAN Toolbox es:

```text
bit 0 -> -1
bit 1 -> +1
```

La polaridad anterior producía 48 diferencias de 48 bits en el L-SIG.


### Receptor Python: Viterbi, BPSK y pilotos

Después de corregir el transmisor para que fuera compatible con WLAN Toolbox,
también fue necesario actualizar el receptor Python.

El Viterbi debe usar la misma orientación del registro convolucional:

```python
next_state = ((state >> 1) | ((bit & 1) << 6)) & 0x7F
```

El demapeo BPSK debe interpretar:

```text
símbolo negativo -> bit 0
símbolo positivo -> bit 1
```

Además, la corrección de fase común de cada símbolo DATA debe usar la polaridad
de pilotos correspondiente a su índice de símbolo. El L-SIG usa el índice 0 y
el primer símbolo DATA usa el índice 1.

El test sintético completo se ejecuta como módulo:

```bash
python -m tests.wifi.python.test_wifi_receiver_v2
```

### Pilotos OFDM DATA

Los pilotos no deben permanecer constantes en todos los símbolos DATA.
Deben seguir la secuencia legacy de polaridad de 127 símbolos.

Mantener pilotos constantes permitía recuperar L-SIG, pero provocaba errores
en DATA y hacía fallar el FCS.

### Vendor IE

WLAN Toolbox puede representar los Information Elements incluyendo bytes
de Element ID y longitud. El parser no debe asumir que el OUI comienza en
la primera posición.

El parser busca la firma:

```text
02 11 22 01 ALBSENS 00
```

y extrae:

- versión;
- transmitter ID;
- experiment ID;
- contador de paquete.

## Tests Python

### `python/export_wifi_beacon_mat.py`

Genera el beacon exacto de Python y lo exporta a MAT para validarlo con
WLAN Toolbox sin utilizar USRP.

Salida:

```text
results/wifi_matlab_rx/python_beacon_waveform.mat
```

Ejecución:

```bash
python -m tests.wifi.python.export_wifi_beacon_mat
```

### `python/export_lsig_debug.py`

Exporta las etapas internas del L-SIG:

- 24 bits originales;
- 48 bits convolucionales;
- 48 bits entrelazados;
- símbolo OFDM temporal.

Salida:

```text
results/wifi_matlab_rx/python_lsig_debug.mat
```

Ejecución:

```bash
python -m tests.wifi.python.export_lsig_debug
```

### `python/test_wifi_receiver_v2.py`

Prueba sintética del receptor Python usando canal multipath, CFO, ruido,
Vendor IE y contador conocido.

Comprueba:

- detección;
- recuperación PHY;
- FCS;
- Vendor IE;
- contador;
- dimensión de la CSI.

Esta prueba verifica consistencia interna, pero la referencia de conformidad
WLAN es MATLAB WLAN Toolbox.

Ejecución:

```bash
python -m tests.wifi.python.test_wifi_receiver_v2
```

## Tests MATLAB

Para ejecutar los tests:

```bash
matlab -sd ~/AlbertoDir/DT_sensing_fusion_WIFI5G \
  -batch "addpath('src/matlab/wifi_sensing','-begin'); addpath('tests/wifi/matlab','-begin'); NOMBRE_TEST"
```

### `matlab/test_python_waveform.m`

Validación end-to-end de la waveform Python sin canal RF.

Comprueba:

- detección PHY;
- L-SIG;
- DATA;
- MPDU;
- FCS;
- Beacon;
- SSID;
- BSSID;
- Vendor IE;
- contador;
- CSI.

Resultado esperado:

```text
RESULT: PYTHON WAVEFORM IS VALID
```

### `matlab/diagnose_python_waveform.m`

Diagnostica:

- L-STF;
- L-LTF;
- potencia;
- conjugación;
- inversión I/Q;
- detección;
- CFO;
- sincronización;
- estimación de canal.

Se utiliza cuando MATLAB no detecta la waveform.

### `matlab/diagnose_python_lsig_data.m`

Diagnostica:

- RATE;
- Reserved;
- LENGTH;
- parity;
- tail;
- MCS;
- DATA;
- MPDU/FCS;
- Beacon;
- Vendor IE.

Se utiliza cuando el preámbulo es válido pero el paquete completo falla.

### `matlab/compare_python_matlab_lsig.m`

Compara el L-SIG Python con una waveform de referencia creada por WLAN
Toolbox:

- muestras temporales;
- FFT;
- bits entrelazados;
- bits convolucionales;
- orden G0/G1;
- pilotos;
- bits L-SIG originales.

### `matlab/export_matlab_pilot_polarity.m`

Extrae la secuencia de polaridad de pilotos de una waveform MATLAB válida.

Salida:

```text
results/wifi_matlab_rx/matlab_pilot_polarity.txt
```

### `matlab/analyze_tx_bursts.m`

Analiza una captura OTA:

- grupos de energía;
- duración de bursts;
- potencia;
- periodicidad;
- autocorrelación de envolvente.

Permite distinguir entre:

- fallo de waveform;
- fallo de transmisión UHD;
- tráfico WiFi ajeno;
- ausencia del periodo esperado.

## Problemas habituales

### MATLAB no encuentra los helpers

Añadir:

```matlab
addpath("src/matlab/wifi_sensing", "-begin");
```

Los principales helpers son:

- `recoverOFDMBits.m`
- `recoverPreamble.m`
- `hWLANPacketDetector.m`
- `hSDRReceiver.m`
- `hSDRBase.m`

### `recoverOFDMBits` no detecta el paquete

Comprobar:

1. sample rate de 20 MHz;
2. L-STF;
3. L-LTF;
4. silencio antes del paquete;
5. amplitud;
6. orden I/Q;
7. CFO.

Usar:

```text
diagnose_python_waveform
```

### L-SIG devuelve `failCheck = 1`

Comprobar:

1. RATE;
2. LENGTH LSB-first;
3. reserved bit;
4. parity;
5. tail;
6. codificador convolucional;
7. interleaver;
8. polaridad BPSK;
9. pilotos.

Usar:

```text
compare_python_matlab_lsig
diagnose_python_lsig_data
```

### DATA se recupera pero falla MPDU/FCS

Comprobar:

1. secuencia de pilotos DATA;
2. scrambler;
3. SERVICE;
4. tail;
5. padding;
6. orden de bits del PSDU;
7. FCS;
8. longitud declarada en L-SIG.

### El tono se recibe, pero el beacon no

La cadena RF funciona. Revisar:

- waveform digital;
- framing UHD;
- continuidad de llamadas `send`;
- underflows;
- periodo real de transmisión.

### La media TX ON y TX OFF cambia poco

Es normal debido al duty cycle bajo. Para un paquete de aproximadamente
240 microsegundos cada 102,4 ms, el duty cycle es aproximadamente 0,23 %.

Son más útiles:

- máximo;
- percentiles muy altos;
- duración de bursts;
- periodicidad;
- decodificación PHY/MAC.

## Archivos generados que no se versionan

No deben subirse capturas ni resultados:

```text
results/wifi_matlab_rx/*.mat
results/wifi_matlab_rx/*.txt
results/wifi_matlab_rx/*.csv
results/wifi_matlab_rx/*.json
results/wifi_rx/*.npz
```
