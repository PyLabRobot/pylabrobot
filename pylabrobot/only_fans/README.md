# A module just for Fans

Running a fan at 100% intensity for one minute:

```python
from pylabrobot.only_fans import Fan
from pylabrobot.only_fans import HamiltonHepaFan

fan = Fan(backend=HamiltonHepaFan(), name="my fan")
await fan.setup()
await fan.turn_on(intensity=100, duration=60)
```
