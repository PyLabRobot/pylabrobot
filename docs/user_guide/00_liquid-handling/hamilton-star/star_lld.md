# Liquid level detection on Hamilton STAR(let)

Liquid level detection (LLD) is a feature that allows the Hamilton STAR(let) to move the pipetting tip down slowly until a liquid is found using either a) the pressure sensor, or b) a change in capacitance, or c) both. This feature is useful if you want to aspirate or dispense at a distance relative to the liquid surface, but you don't know the exact height of the liquid in the container.

To use LLD, you need to specify the LLD mode when calling the `aspirate` or `dispense` methods. Here is how you can use pressure or capacative LLD with the `aspirate` :

```python
await lh.aspirate([tube], vols=[300], lld_mode=[STAR.LLDMode.GAMMA])
```

The `lld_mode` parameter can be one of the following:

- `STAR.LLDMode.OFF`: default, no LLD
- `STAR.LLDMode.GAMMA`: capacative LLD
- `STAR.LLDMode.PRESSURE`: pressure LLD
- `STAR.LLDMode.DUAL`: both capacative and pressure LLD
- `STAR.LLDMode.Z_TOUCH_OFF`: find the bottom of the container

The `lld_mode` parameter is a list, so you can specify a different LLD mode for each channel.

```{note}
The `lld_mode` parameter is only avilable when using the `STAR` backend.
```

## Catching errors

All channelized pipetting operations raise a `ChannelizedError` exception when an error occurs, so that we can have specific error handling for each channel.

When no liquid is found in the container, the channel will have a `TooLittleLiquidError` error. This is useful for detecting that your container is empty.

You can catch the error like this:

```python
from pylabrobot.liquid_handling.errors import ChannelizedError
from pylabrobot.resources.errors import TooLittleLiquidError
channel = 0
try:
  await lh.aspirate([tube], vols=[300], lld_mode=[STAR.LLDMode.GAMMA], use_channels=[channel])
except ChannelizedError as e:
  if isinstance(e.errors[channel], TooLittleLiquidError):
    print("Too little liquid in tube")
```
