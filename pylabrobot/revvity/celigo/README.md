# Celigo

A direct PyLabRobot driver for the Revvity Celigo image cytometer (formerly sold by
Nexcelom/Cyntellect). `Celigo` is the main entry point and owns its camera, galvo, and
laser components. It talks to the FTDI USB-IO controller without requiring the Celigo
application.

## Package

| Module | Responsibility |
|---|---|
| `celigo.py` | Device lifecycle, FTDI protocol, illumination, acquisition, autofocus, and diagnostics |
| `motion.py` | Stepper motors, linear axes, filter wheels, homing, and encoder motion |
| `camera.py` | Async Lumenera SDK capture and dependency-free raw image frames |
| `galvo.py` | Galvo positioning, calibration, status, and calibrated voltage conversion |
| `laser.py` | Guarded laser UART commands, firing, galvo targeting, and laser optics |
| `config.py` | Typed loaders for the vendor hardware, optical calibration, and channel configuration |
| `coordinates.py` | Pixel, sample-mm, and stage-mm coordinate frames |
| `navigation.py` | Plate/well navigation and galvo FOV planning |

Channel selector bits, intensities, logical filter mappings, axis profiles, loading
currents, and drawer return coordinates are read or derived from the copied Celigo
configuration. Selecting a logical filter requires a known filter-wheel home position.

## Usage

```python
from pylabrobot.revvity import Celigo, CeligoConfig
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

config = CeligoConfig.from_install("/path/to/Celigo/ConfigFiles")
cel = Celigo(
  config=config,
  usb_address="3-2",  # optional USB bus/port path when more than one FTDI is attached
  lucam_sdk="/path/to/liblucamapi.so",  # optional when discoverable or set in the environment
)
cel.set_plate(Cor_96_wellplate_360ul_Fb(name="imaging_plate"))
await cel.setup()
try:
  status = await cel.request_controller_status()
  if status.busy:
    await cel.wait_for_controller_ready()

  result = await cel.acquire("A1", "green", autofocus="image")
  result.frame.save_pgm("A1-green.pgm")
finally:
  await cel.stop()
```

Linear-axis movement is expressed in millimeters; encoder conversion is internal. For
example, `await cel.z_axis.move_to(5.0)` moves the focus axis to 5.0 mm. Use
`cel.x_axis`, `cel.y_axis`, and `cel.z_axis` for axis-specific homing, position reads,
and movement. Well navigation uses the geometry of the assigned PyLabRobot plate
resource.

Filter wheels are components too: use `cel.dichroic_filter.move_to(logical_position)`,
`cel.camera_filter.move_to(logical_position)`, or their `home()` methods. A wheel must
be homed before its first logical-position move. Set `LUCAM_SDK_LIBRARY` or pass
`lucam_sdk=...` to `Celigo` if the Lumenera SDK library is not discoverable by the
operating system.

The default objective is 3X, matching the vendor startup sequence. Galvo imaging
centers, per-filter offsets, voltage inversion/bounds, channel Z offsets, and channel
pixel-scale corrections come from the copied Celigo configuration. `autofocus="hardware"`
is rejected because the displacement-sensor interface is not yet implemented; use
`autofocus="image"` for host-side image autofocus.

`Celigo.setup()` opens both the controller and its configured camera, and `Celigo.stop()`
closes both. Setup also establishes position references by homing Z, X, Y, and the
dichroic filter in that clearance-safe order. It reads the native Lumenera format and,
when necessary, applies a centered ROI matching `CalibrationConfig.xml`. On the installed
camera this changes `2464x2056` to the calibrated `2048x2048` window at offset `(208, 4)`;
the ROI and a 4,194,304-byte frame have been verified on hardware. Format changes are
read back and calibrated acquisition still fails closed on any mismatch. Camera SDK
calls are serialized and time-bounded; after a timeout the camera remains poisoned until
a deferred close completes.

`select_channel()` selects the filter, galvo center, and lamp routing while keeping every
light off. Use `set_illumination_enabled(True, intensity_percent=...)` for direct
component work; `acquire()` handles illumination automatically and extinguishes it
before returning, including after a failed capture.

Machine-read commands use the `request_*` prefix. `request_controller_status()` returns a
`ControllerStatus` dataclass with named fields such as `busy`, `error`,
`interlock_open`, and `controller_failed`, plus the original `raw_flags` and
`extended_status` values.

Galvo operations live on the owned `cel.galvo` component:

```python
await cel.galvo.home()
await cel.galvo.move_single("x", logical_voltage=1.5, timeout=6.0)  # volts, seconds
status = await cel.galvo.request_controller_status()
```

Laser operations live on the owned `cel.laser` component:

```python
config = CeligoConfig.from_install(config_root)
cel = Celigo(config=config, allow_laser=True)
await cel.setup()
await cel.laser.send_command("...")
response = await cel.laser.request_uart_response()
await cel.laser.fire(laser_index=0, shots=1, delay=0.01)  # seconds
```

Targeted firing through `cel.laser.fire_targets()` uses the selected laser's calibrated
X/Y center unless `center_voltages=` is supplied explicitly. Normal `stop()` best-effort
aborts controller work and clears all four analog and twelve digital outputs before
closing FTDI.

`usb_address` uses the Linux USB topology form `<bus>-<port>[.<port>...]`; omit it when
the Celigo FTDI is uniquely identifiable by `device_id` or is the only matching FTDI.

`CeligoConfig` is one complete per-instrument configuration and is required by `Celigo`.
Load it with `CeligoConfig.from_install(config_root)`, then pass it to the constructor.
The loader accepts the Celigo install root, its `ConfigFiles` directory, or the hardware
configuration file itself. The path is explicit; no global installation search is
performed. It indexes the configuration directory once and requires all companion
calibration files:

```python
from pylabrobot.revvity import CeligoConfig

config = CeligoConfig.from_install(config_root)
config.hardware_defaults.default_calibrated_z += 0.01
cel = Celigo(config=config)
```

Individual mechanisms inside `config.hardware` remain optional because instrument builds
can omit them; the top-level hardware, optical/stage calibration, illumination,
hardware-default, galvo-calibration, and navigation configuration objects are required.

Live hardware verification covers controller startup/status/self-test, native XYZ/filter
homing, drawer open/close, galvo calibration/centering, native and calibrated camera
capture, machine auto-exposure, image autofocus, camera-trigger diagnostics, all five
configured illumination channels, and a 16-FOV galvo scan.
Fluorescence output switching, filter movement, and acquisition are verified. Laser
firing has not been exercised and remains blocked by the instrument's asserted generic
interlock. `cel.laser` is disabled unless the `Celigo` constructor receives
`allow_laser=True`, and should only be enabled after independently establishing a safe
instrument state.

## Tests

```console
pytest pylabrobot/revvity/celigo/tests
```
