# Centrifuges

Centrifuges are controlled by the {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class. This class takes a backend as an argument. The backend is responsible for communicating with the centrifuge and is specific to the hardware being used.

The {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class has a number of methods for controlling the centrifuge. These are:

- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.open_door`: Open the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.close_door`: Close the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_door`: Lock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_door`: Unlock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_bucket`: Lock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_bucket`: Unlock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket1`: Rotate to Bucket 1.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket2`: Rotate to Bucket 2.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.rotate_distance`: Rotate the buckets a specified distance (8000 = 360 degrees).
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.start_spin_cycle`: Start centrifuge spin cycle.

PLR supports the following centrifuges:

```{toctree}
:maxdepth: 1

agilent_vspin
```
