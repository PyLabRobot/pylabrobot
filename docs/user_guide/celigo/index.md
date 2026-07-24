# Celigo

```{toctree}
:maxdepth: 1

celigo/hello-world
```

PyLabRobot controls the Celigo through one plain `Celigo` class. Configuration copied
from the vendor installation supplies motor limits, channels, optical centers, filter
positions, and plate navigation data.

Direct controller communication, native XYZ and filter homing, drawer motion, galvo
calibration/centering, brightfield output, and raw Lumenera capture have been exercised
on a live instrument. Fluorescence imaging, image autofocus on a real sample,
hardware-displacement autofocus, triggered acquisition, and laser operations have not.
Laser commands require an explicit safety opt-in and a passing controller interlock.

The tested camera currently reports its full `2464x2056` sensor while the installed
calibration requires `2048x2048`. Calibrated `acquire()` therefore fails closed as
designed. Raw full-sensor capture works; native ROI programming is still required before
the calibrated acquisition and autofocus paths can be evaluated without vendor software.

After `setup()`, home Z, X, and Y with `home()` before requesting XYZ motion. Homing
checks encoder response, proves negative-limit activation and release, establishes the
configured index datum, restores the controller mode, and verifies the final in-range
position. Controller serial I/O uses the instrument FTDI interface at 230400 baud; the
Lumenera camera is a separate USB connection through its SDK. See the Hello World
notebook for direct USB and USB/IP setup, a native homing/drawer cycle, and raw
brightfield capture.
