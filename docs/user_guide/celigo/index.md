# Celigo

```{toctree}
:maxdepth: 1

celigo/hello-world
celigo/advanced-imaging
celigo/components-and-diagnostics
```

PyLabRobot controls the Celigo through one plain `Celigo` class. Configuration copied
from the vendor installation supplies motor limits, channels, optical centers, filter
positions, and plate navigation data.

Load that configuration explicitly when constructing the instrument:

```python
from pylabrobot.celigo import Celigo, CeligoConfig

config = CeligoConfig.from_install("/path/to/Celigo/ConfigFiles")
celigo = Celigo(config=config)
```

Live verification covers controller setup/status/self-test, native XYZ and filter
homing, drawer motion, galvo calibration/centering, native and calibrated Lumenera
capture, image autofocus on an A1 cell sample, machine auto-exposure, all five configured
illumination channels, camera-trigger diagnostics, and a 16-FOV galvo scan. Fluorescence
output switching, filter motion, and acquisition are verified,
but signal quality was not characterized without a fluorescent reference sample.
Hardware-displacement autofocus is not implemented, and externally triggered frame
acquisition and laser firing have not been exercised.

Laser commands require an explicit safety opt-in and a passing controller interlock.
They are exposed through the component owned by the instrument, for example
`await celigo.laser.fire(...)` and `await celigo.laser.send_command(...)`.

The camera reports its native `2464x2056` sensor while the installed calibration
requires `2048x2048`. Setup programs and reads back the calibrated centered ROI at
offset `(208, 4)`, and calibrated acquisition fails closed if the resulting camera
geometry does not match the configuration.

After `setup()`, call `await celigo.home_imaging_axes()` or home individual components with
`celigo.z_axis.home()`, `celigo.x_axis.home()`, `celigo.y_axis.home()`, and
`celigo.dichroic_filter.home()`. Homing checks encoder response, proves negative-limit
activation and release, establishes the configured index datum, restores the controller
mode, and verifies the final in-range position. Controller serial I/O uses the
instrument FTDI interface at 230400 baud; the Lumenera camera is a separate USB
connection through its SDK. See the Hello World notebook for direct USB and USB/IP
setup, a native homing/drawer cycle, and brightfield capture.

The Advanced Imaging notebook covers coordinate transforms, frame analysis, exposure,
autofocus, structured acquisition results, and calibrated galvo FOV planning. The
Hardware Components and Diagnostics notebook covers axes, filter wheels, controller
I/O, galvo diagnostics, barcode and trigger interfaces, active self-tests, and the
laser component's explicit safety boundary.
