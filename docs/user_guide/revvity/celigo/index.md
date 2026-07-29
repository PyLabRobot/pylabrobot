# Celigo

```{toctree}
:maxdepth: 1

hello-world
advanced-imaging
components-and-diagnostics
```

[Product page](https://www.revvity.com/product/celigo-5c-config-200-bffl-5c)

PyLabRobot controls the Celigo through one plain `Celigo` class. Configuration copied
from the vendor installation supplies motor limits, channels, optical centers, filter
positions, and the instrument coordinate calibration. The assigned PyLabRobot `Plate`
resource supplies the plate and well geometry used for navigation.

Load the instrument configuration explicitly, then set the plate:

```python
from pylabrobot.revvity import Celigo, CeligoConfig
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

config = CeligoConfig.from_install("/path/to/Celigo/ConfigFiles")
celigo = Celigo(config=config)
celigo.set_plate(Cor_96_wellplate_360ul_Fb(name="imaging_plate"))
```

Opening the drawer and closing it to a calibrated sample-relative coordinate do not
depend on a plate. Use `close_drawer_to_sample_mm(x_mm, y_mm)` for custom carriers.
Closing to a named well, moving to wells, and acquiring well images require
`set_plate()` first.

Live verification covers controller setup/status/self-test, native XYZ and filter
homing, drawer motion, galvo calibration/centering, native and calibrated Lumenera
capture, image autofocus on an A1 cell sample, machine auto-exposure, all five configured
illumination channels, camera-trigger diagnostics, and a 16-FOV galvo scan. Fluorescence
output switching, filter motion, and acquisition are verified.
Hardware-displacement autofocus is not implemented, and externally triggered frame
acquisition and laser firing have not been exercised.

Laser commands require an explicit safety opt-in and a passing controller interlock.
They are exposed through the component owned by the instrument, for example
`await celigo.laser.fire(...)` and `await celigo.laser.send_command(...)`.

The camera reports its native `2464x2056` sensor while the installed calibration
requires `2048x2048`. Setup programs and reads back the calibrated centered ROI at
offset `(208, 4)`, and calibrated acquisition fails closed if the resulting camera
geometry does not match the configuration.

`setup()` homes Z, X, Y, and the dichroic filter in that order. Homing checks encoder
response, proves negative-limit activation and release, establishes the configured index
datum, restores the controller mode, and verifies the final in-range position. Controller
serial I/O uses the instrument FTDI interface at 230400 baud; the Lumenera camera is a
separate USB connection through its SDK. See [Celigo Hello World](hello-world.ipynb)
for direct USB setup, a native homing/drawer cycle, and brightfield capture.

[Advanced Imaging](advanced-imaging.ipynb) covers coordinate transforms, frame
analysis, exposure, autofocus, structured acquisition results, and calibrated galvo FOV
planning. [Hardware Components and Diagnostics](components-and-diagnostics.ipynb)
covers axes, filter wheels, controller I/O, galvo diagnostics, barcode and trigger
interfaces, active self-tests, and the laser component's explicit safety boundary.
