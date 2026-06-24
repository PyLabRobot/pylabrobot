# Centrifuges

Centrifuges are controlled by the {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class. This class takes a backend as an argument. The backend is responsible for communicating with the centrifuge and is specific to the hardware being used.

The {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class has a number of methods for controlling the centrifuge. These are:

- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.open_door`: Open the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.close_door`: Close the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_door`: Lock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_door`: Unlock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_bucket`: Lock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_bucket`: Unlock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket1`: Rotate/present Bucket 1.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket2`: Rotate/present Bucket 2.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.spin`: Start a centrifuge spin cycle.

Some standalone door/lock and low-level rotation primitives are hardware-dependent. For example,
the Agilent VSpin backend exposes {meth}`~pylabrobot.centrifuge.vspin_backend.VSpinBackend.go_to_position`
for arbitrary bucket positioning during calibration (8000 ticks = 360 degrees), while the HighRes
MicroSpin only exposes bucket presentation through `go_to_bucket1()` / `go_to_bucket2()` and manages
door/lock operations automatically. See the hardware-specific guides below for details.

PLR supports the following centrifuges:

```{toctree}
:maxdepth: 1

agilent_vspin
highres_microspin
```
