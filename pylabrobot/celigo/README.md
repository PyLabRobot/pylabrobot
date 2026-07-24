# Celigo

A direct PyLabRobot driver for the Nexcelom/Cyntellect Celigo image cytometer. The
public API is one plain `Celigo` class; it talks to the FTDI USB-IO controller without
requiring the Celigo application.

## Package

| Module | Responsibility |
|---|---|
| `celigo.py` | Device lifecycle, FTDI protocol, motion, illumination, acquisition, autofocus, laser safety, and diagnostics |
| `camera.py` | Async Lumenera SDK capture and dependency-free raw image frames |
| `config.py` | Typed loaders for the vendor hardware, optical calibration, and channel configuration |
| `transforms.py` | Encoder-tick, stage-mm, galvo-voltage, and DAC conversions |
| `coordinates.py` | Pixel, sample-mm, and stage-mm coordinate frames |
| `navigation.py` | Plate/well navigation and galvo FOV planning |

Channel selector bits, intensities, logical filter mappings, axis profiles, loading
currents, and drawer return coordinates are read or derived from the copied Celigo
configuration. Selecting a logical filter requires a known filter-wheel home position.

## Usage

```python
from pylabrobot.celigo import Celigo, CeligoHardwareConfig
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

cel = Celigo(
  install_dir="/path/to/Celigo/ConfigFiles",
  usb_address="3-2",  # optional USB bus/port path when more than one FTDI is attached
  lucam_sdk="/path/to/liblucamapi.so",  # optional when discoverable or set in the environment
)
cel.plate = Cor_96_wellplate_360ul_Fb(name="imaging_plate")
await cel.setup()
try:
  status = await cel.request_status()
  if status.busy:
    await cel.wait_for_ready()

  # Home Z first for vertical clearance, then establish X/Y encoder datums.
  for axis in ("z", "x", "y"):
    await cel.home(axis)
  await cel.home_filter_accurate()
  result = await cel.acquire("A1", "green", autofocus="image")
  result.frame.save_pgm("A1-green.pgm")
finally:
  await cel.stop()
```

`Celigo.move()` and `Celigo.move_z()` take millimeters; encoder conversion is internal.
For example, `await cel.move_z(5.0)` moves the focus axis to 5.0 mm. The Corning 3603 resource's
Celigo-specific registration correction is applied internally by model name.

For an already-homed filter wheel, use `set_filter_home_position(ticks)` instead of
homing it again. Set `LUCAM_SDK_LIBRARY` or pass `lucam_sdk=...` to `Celigo` if the
Lumenera SDK library is not discoverable by the operating system.

The default objective is 3X, matching the vendor startup sequence. Galvo imaging
centers, per-filter offsets, voltage inversion/bounds, channel Z offsets, and channel
pixel-scale corrections come from the copied Celigo configuration. `autofocus="hardware"`
is rejected because the displacement-sensor interface is not yet implemented; use
`autofocus="image"` for host-side image autofocus.

`Celigo.setup()` opens both the controller and its configured camera, and `Celigo.stop()`
closes both. Setup reads the native Lumenera format and, when necessary, applies a
centered ROI matching `CalibrationConfig.xml`. On the installed camera this changes
`2464x2056` to the calibrated `2048x2048` window at offset `(208, 4)`; the ROI and a
4,194,304-byte frame have been verified on hardware. Format changes are read back and
calibrated acquisition still fails closed on any mismatch. Camera SDK calls are
serialized and time-bounded; after a timeout the camera remains poisoned until a
deferred close completes.

Machine-read commands use the `request_*` prefix. `request_status()` returns a
`ControllerStatus` dataclass with named fields such as `busy`, `error`,
`interlock_open`, and `controller_failed`, plus the original `raw_flags` and
`extended_status` values.

Targeted firing uses the selected laser's calibrated X/Y center unless `center_volts=`
is supplied explicitly. Normal `stop()` best-effort aborts controller work and clears
all four analog and twelve digital outputs before closing FTDI.

`usb_address` uses the Linux USB topology form `<bus>-<port>[.<port>...]`; omit it when
the Celigo FTDI is uniquely identifiable by `device_id` or is the only matching FTDI.

`Celigo` loads `HardwareDefaultConfig.xml` whenever `hardware_defaults` is not supplied,
including when the config root comes only from `CELIGO_INSTALL_DIR`. A user who wants
the resolved path or to load the file explicitly can say:

```python
hardware_defaults_path = CeligoHardwareConfig.locate_config_file(
  config_root, "HardwareDefaultConfig.xml"
)
```

`config_root` may be `None` when `CELIGO_INSTALL_DIR` is set.

Live hardware verification currently covers controller startup, native XYZ/filter
homing, drawer open/close, galvo calibration/centering, brightfield output, native camera
ROI, and calibrated camera capture. It does not yet cover fluorescence imaging,
autofocus on a real sample, triggered acquisition, or laser firing. Laser operations are disabled unless
`allow_laser=True`, and should only be enabled after independently establishing a safe
instrument state.

## Tests

```console
pytest pylabrobot/celigo/tests
```
