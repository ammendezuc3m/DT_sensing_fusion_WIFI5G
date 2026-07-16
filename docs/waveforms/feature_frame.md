# FeatureFrame

Estructura común enviada por el receptor al modelo.

## Metadatos comunes

- protocol_version
- waveform_type
- profile_id
- transmitter_id
- experiment_id
- packet_counter
- sequence_number
- rx_timestamp_ns
- center_frequency_hz
- sample_rate_hz
- snr_db
- cfo_hz
- rssi_proxy_dbfs

## Datos específicos

Cada waveform añade un bloque específico:

### WIFI_NONHT_BEACON

- CSI compleja por subportadora
- SSID
- BSSID
- FCS válido
- PHY rate
- longitud MPDU

### BF_TRAINING

- beam_id
- codebook_id
- potencia por beam
- fase
- correlación
- vector de canal

### NR_SSB

- PCI
- iSSB
- PSS correlation
- SSS correlation
- RSRP
- CFO
- estimación de canal
