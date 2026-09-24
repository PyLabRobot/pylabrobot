# Celigo

```{toctree}
:maxdepth: 1

hello-world
advanced-imaging
scan-planning
components-and-diagnostics
```

[Product page](https://www.revvity.com/product/celigo-5c-config-200-bffl-5c)

PyLabRobot controls the Celigo directly through its FTDI controller and Lumenera camera.
Instrument setup, safety boundaries, physical scan planning, imaging, and diagnostics
are covered by the notebooks above.

[Celigo Hello World](hello-world.ipynb) introduces configuration, setup, homing, drawer
motion, channels, basic acquisition, and the one-call well scan. [Advanced
Imaging](advanced-imaging.ipynb) covers coordinate transforms, exposure, autofocus,
structured results, multichannel capture, and calibrated galvo imaging. [Physical Scan
Planning](scan-planning.ipynb) covers reusable scan specifications, arbitrary block
shapes, physical points and bounds, estimates, and inspected execution. [Hardware Components and
Diagnostics](components-and-diagnostics.ipynb) covers low-level components, controller
I/O, active self-tests, and laser safety.
