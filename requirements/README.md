# Python environments

This project uses two Python environments.

---

## 1. Inference environment: `.venv`

Used for PyTorch training, model evaluation, online inference, JSON export and SCP export.

Create:

```bash
cd ~/AlbertoDir/DT_sensing_fusion

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/requirements-inference.txt
```

Export current requirements:

```bash
cd ~/AlbertoDir/DT_sensing_fusion

.venv/bin/python -m pip freeze > requirements/requirements-inference.txt
```

---

## 2. UHD / SDR capture environment: `.venv_uhd`

Used for direct USRP B210 access from Python.

The UHD Python binding is installed through APT, not pip.

Install system packages:

```bash
sudo apt update
sudo apt install uhd-host python3-uhd
```

Create the environment with system packages visible:

```bash
cd ~/AlbertoDir/DT_sensing_fusion

python3 -m venv --system-site-packages .venv_uhd
source .venv_uhd/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/requirements-uhd.txt
```

Check UHD:

```bash
source .venv_uhd/bin/activate

python - <<'PY'
import uhd

usrp = uhd.usrp.MultiUSRP("serial=34B73C3")
print("USRP OK")
print("RX channels:", usrp.get_rx_num_channels())
print("Motherboard:", usrp.get_mboard_name())
PY
```

Important: Ubuntu `python3-uhd` may be compiled against NumPy 1.x. For this reason, `.venv_uhd` should use `numpy<2`.

---

## 3. Requirements files

Expected files:

```text
requirements/
├── README.md
├── apt-requirements.txt
├── requirements-inference.txt
└── requirements-uhd.txt
```

Recommended `requirements/requirements-uhd.txt`:

```text
numpy<2
scipy
h5py
```

Recommended `requirements/apt-requirements.txt`:

```text
uhd-host
python3-uhd
libuhd4.6.0t64
libgnuradio-uhd3.10.9t64
soapysdr0.8-module-uhd
```
