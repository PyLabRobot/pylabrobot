# Containers

Resources that contain liquid are subclasses of {class}`pylabrobot.resources.container.Container`. This class provides a {class}`pylabrobot.resources.volume_tracker.VolumeTracker` that helps {class}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler` keep track of the liquid in the resource. (For more information on trackers, check out {doc}`/user_guide/using-trackers`). Examples of subclasses of `Container` are {class}`pylabrobot.resources.Well` and {class}`pylabrobot.resources.trough.Trough`.

It is possible to instantiate a `Container` directly:

```python
from pylabrobot.resources import Container
container = Container(name="container", size_x=10, size_y=10, size_z=10)
# volume is computed by assuming the container is a cuboid, and can be adjusted with the max_volume
# parameter
```
