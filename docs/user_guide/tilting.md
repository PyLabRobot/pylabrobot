# Tilting

Currently only the Hamilton tilt module is supported.

```python
from pylabrobot.tilting.hamilton import HamiltonTiltModule

tilter = HamiltonTiltModule(name="tilter", com_port="COM1")

await lh.move_plate(my_plate, tilter)

await tilter.set_angle(10) # absolute angle, clockwise, in degrees
await tilter.tilt(-1) # relative
```
