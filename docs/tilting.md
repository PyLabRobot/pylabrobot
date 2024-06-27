# Tilting

Currently only the Hamilton tilt module is supported.

```python
from pylabrobot.tilting.hamilton import HamiltonTiltModule

tilter = HamiltonTiltModule(name="tilter", com_port="COM1")
lh.move_plate(my_plate, tilter)

tilter.set_angle(10)
tilter.tilt(-1) # relative
```
