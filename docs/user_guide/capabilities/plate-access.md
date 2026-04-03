# Plate Access

The plate access capability standardizes a narrow but common class of machine interactions:
locking an instrument, presenting an access path for the source or destination side, polling
access state, and closing the door afterwards.

This is useful for devices where the user-facing control surface is about getting hardware into
an accessible state rather than immediately running a transfer or assay.

## API

```python
from pylabrobot.capabilities.plate_access import PlateAccessState
```

Capability methods:

- `lock(app=None, owner=None)`
- `unlock()`
- `get_access_state()`
- `open_source_plate()`
- `close_source_plate(plate_type=None, barcode_location=None, barcode="")`
- `open_destination_plate()`
- `close_destination_plate(plate_type=None, barcode_location=None, barcode="")`
- `close_door()`

`get_access_state()` returns a `PlateAccessState` with normalized fields for:

- source access open/closed
- destination access open/closed when the backend can infer them
- door open/closed
- source and destination plate position values when available
- a `raw` dictionary with the backend's native state payload

## Echo Example

```python
import asyncio

from pylabrobot.labcyte import Echo


async def main():
  async with Echo(host="192.168.0.25") as echo:
    info = await echo.get_instrument_info()
    print(info.model, info.serial_number)

    await echo.lock()
    try:
      baseline = await echo.get_access_state()
      print("baseline:", baseline)

      await echo.open_source_plate()
      opened = await echo.get_access_state()
      print("opened:", opened)

      await echo.close_source_plate()
      await echo.close_door()
    finally:
      await echo.unlock()


asyncio.run(main())
```

For the Echo integration, motion commands require an active lock. Read-only polling and
instrument info queries do not.
