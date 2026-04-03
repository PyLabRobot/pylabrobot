# Echo

The Labcyte Echo integration currently targets the Medman access-control surface validated against
an Echo 650. This first pass focuses on safe mechanical access operations rather than liquid
transfer execution.

Supported operations:

- fetch instrument identity with `get_instrument_info()`
- lock and unlock the instrument session
- poll `GetDIOEx2` through `get_access_state()`
- present and retract the source-side access path
- present and retract the destination-side access path
- close the door

The driver matches the Echo's observed transport quirks:

- HTTP-like `POST /Medman`
- gzip-compressed SOAP bodies
- LF-terminated request headers
- token-based client identity reused for lock ownership

## Safe Source Access Cycle

```python
import asyncio

from pylabrobot.labcyte import Echo


async def main():
  async with Echo(host="192.168.0.25") as echo:
    await echo.lock()
    try:
      await echo.open_source_plate()
      state = await echo.get_access_state()
      print(state.raw)

      await echo.close_source_plate()
      await echo.close_door()
    finally:
      await echo.unlock()


asyncio.run(main())
```

## Scope

This integration does not yet implement:

- event stream registration on port `8010`
- plate surveys
- transfer planning or `DoWellTransfer`
- plate maps or survey uploads
