# Aravis Camera Driver for PLR's Cytation

**Status**: Proof-of-concept, tested on real hardware (Cytation 1 + BlackFly BFLY-U3-13S2M)

Replaces PySpin with [Aravis](https://github.com/AravisProject/aravis) for controlling the BlackFly camera inside the Cytation. No Spinnaker SDK needed. No Python version cap.

## Why

PLR's `CytationBackend` uses PySpin (FLIR Spinnaker SDK) for camera control. PySpin only has pre-built wheels up to Python 3.10, blocking PLR from using newer Python versions. Aravis is an open-source alternative that talks to the camera directly via GenICam/USB3 Vision — no vendor SDK at all.

## What's Here

```
aravis_camera.py       # Standalone camera driver (Aravis/GenICam)
aravis_simulated.py    # Simulated camera for testing without hardware
cytation_aravis.py     # CytationAravisBackend — drop-in for CytationBackend
```

### aravis_camera.py

Standalone camera driver. No PLR dependencies (just numpy). Handles:
- Camera discovery by serial number
- Software trigger (single-frame capture)
- Exposure/gain control via GenICam nodes
- Buffer management (pre-allocated pool)
- Clean connect/disconnect

Can be used independently or composed into `CytationAravisBackend`.

### aravis_simulated.py

Same API as `AravisCamera` but returns synthetic numpy arrays. No Aravis, no GObject, no hardware needed. For testing and CI.

### cytation_aravis.py

`CytationAravisBackend(BioTekBackend, MicroscopyBackend)` — uses `AravisCamera` for the camera and PLR's `BioTekBackend` for the Cytation serial protocol (filter wheel, objective turret, focus motor, LED, stage positioning). Drop-in replacement for `CytationBackend`.

## Installation

### 1. Aravis system library

```bash
# macOS
brew install aravis

# Linux
sudo apt-get install libaravis-dev gir1.2-aravis-0.8
```

### 2. Python dependencies

```bash
pip install PyGObject numpy
```

### 3. Drop files into PLR

Copy the three `.py` files to `pylabrobot/agilent/biotek/` in your PLR installation.

## Usage

### Standalone (camera only, no Cytation serial)

```python
from aravis_camera import AravisCamera

camera = AravisCamera()

# Discover
cameras = AravisCamera.enumerate_cameras()
print(cameras)  # [CameraInfo(serial='...', model='Blackfly ...', ...)]

# Connect, capture, disconnect
await camera.setup(cameras[0].serial_number)
await camera.set_exposure(10.0)  # ms
await camera.set_gain(1.0)
image = await camera.trigger()   # numpy array (height, width), uint8
print(image.shape)               # (964, 1288)
await camera.stop()
```

### With PLR Cytation backend

```python
from pylabrobot.agilent.biotek.cytation_aravis import CytationAravisBackend
from pylabrobot.capabilities.microscopy.standard import ImagingMode, Objective

backend = CytationAravisBackend(camera_serial="your_serial_here")
await backend.setup(use_cam=True)

# Same capture API as CytationBackend — serial protocol + camera
res = await backend.capture(
    row=1, column=1,
    mode=ImagingMode.BRIGHTFIELD,
    objective=Objective.O_4X_PL_FL,
    focal_height=3.0,
    exposure_time=5,
    gain=16,
    plate=your_plate,
    led_intensity=5,
)

# res.images[0] is a numpy array
await backend.stop()
```

## What It Replaces

```
BEFORE:                              AFTER:
CytationBackend                      CytationAravisBackend
  ├─ serial protocol (BioTek)          ├─ serial protocol (same)
  └─ PySpin → Spinnaker SDK            └─ AravisCamera → Aravis
       (proprietary, Python ≤3.10)          (open source, any Python)
```

Only the camera layer changes. The Cytation serial protocol (filter wheel, objectives, focus, LED, stage) is identical.

## Tested On

- Cytation 1 (firmware 1.02)
- BlackFly BFLY-U3-13S2M (USB3 Vision)
- Aravis 0.8.35
- macOS, Python 3.12
- Camera acquisition, exposure/gain control, LED, stage movement, focus motor — all working
- Gen5 (BioTek software) still works after Aravis testing — no damage to instrument state

## Known Gotchas

1. **1-second delay after trigger mode change** — BlackFly cameras need `asyncio.sleep(1)` after setting TriggerMode=On. Already handled in `AravisCamera.setup()`.

2. **Don't open camera during enumeration** — `Aravis.Camera.new()` locks the USB device. `enumerate_cameras()` uses lightweight descriptor reads instead.

3. **Camera serial has two formats** — Aravis device list returns hex (e.g., `010B6B10`), GenICam returns decimal (`17525520`). `setup()` handles both.

## PLR Branch

Built against PLR's `capability-architecture` branch (commit 226e6d41). Import paths differ from released PLR (`main`). See the analysis doc for details.

---

*Built with [Claude Code](https://claude.ai/claude-code) by Vincent de Boer*
