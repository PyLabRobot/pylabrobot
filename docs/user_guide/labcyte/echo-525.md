# Echo 525

The Labcyte **Echo 525** speaks the exact same Medman protocol as the [Echo 650](echo): the same
`POST /Medman` transport, gzip-compressed SOAP bodies, RPC method set, lock/session model, plate
survey, and `DoWellTransfer` `<wp>` layout. Selecting `model="Echo 525"` therefore reuses the entire
Echo 650 implementation and overrides only the handful of defaults that differ on the 525.

Everything on the [Echo](echo) page — access cycles, surveys, transfers, loading/ejecting,
calibration, plate definitions — applies unchanged. This page covers only the 525-specific parts.

## What differs from the Echo 650

The one behavioural difference is **transfer volume granularity**: the Echo 525 dispenses in
**25 nL** increments, where the Echo 650 uses 2.5 nL. The instrument confirms this over the wire —
`GetTransferVolIncrNl` and `GetTransferVolMinimumNl` both return `25`. Requested volumes must be a
whole multiple of 25 nL; anything else is rejected during transfer planning:

```python
from pylabrobot.labcyte import Echo

echo = Echo("192.168.0.25", model="Echo 525")
# 150 nL is fine (6 x 25 nL); 10 nL would raise ValueError on a 525.
```

The defaults below were reverse-engineered from a Wireshark capture of a physical Echo 525
(`Model` = `Echo 525`, software `2.7.3`) running a HiFi PCR reformat, so they reflect real device
traffic rather than assumptions.

| Default | Echo 525 | Echo 650 |
|---------|----------|----------|
| `transfer_volume_increment_nl` | `25.0` | `2.5` |
| `protocol_version` | `2.6` | `3.1` |
| `client_version` | `2.7.3` | `3.1.0` |
| reported `Model` | `Echo 525` | `Echo 650` |

These are defined per model in `ECHO_MODELS` in `echo.py`; add or adjust an entry there to support
another Echo variant or newer firmware.

## Running a transfer

`Echo(model="Echo 525")` is a drop-in replacement for the 650; the transfer API is identical:

```python
import asyncio

from pylabrobot.labcyte import Echo

async def main(source_plate, destination_plate):
  async with Echo("192.168.0.25", model="Echo 525") as echo:
    await echo.lock()
    try:
      result = await echo.transfer(
        [(source_plate.get_well("A1"), destination_plate.get_well("B1"), 150)],  # nL, multiple of 25
        source_plate_type="6RES_AQ_BP2",
        destination_plate_type="384PP_AQ_BP2",
      )
      print(len(result.transfers), len(result.skipped))
    finally:
      await echo.unlock()
```

## Hardware-free testing with the mock server

`EchoMockServer` is an in-process `asyncio` server that emulates the Echo Medman protocol and
replays real responses captured from a physical Echo 525. It lets you exercise the full
`Echo` stack — setup, lock, survey, `DoWellTransfer`, unlock — with no instrument
attached:

```python
import asyncio

from pylabrobot.labcyte import Echo, EchoMockServer

async def main():
  async with EchoMockServer() as srv:
    echo = Echo(srv.host, model="Echo 525", rpc_port=srv.port)
    await echo.setup()

    info = await echo.get_instrument_info()
    assert info.model == "Echo 525"

    await echo.driver.lock()
    report = await echo.driver.do_well_transfer(
      '<?xml version="1.0"?><Protocol Name="demo"><Name/>'
      '<Layout><wp n="A2" dn="A1" v="150"/></Layout></Protocol>'
    )
    await echo.driver.unlock()
    print(report.succeeded, len(report.transfers))

asyncio.run(main())
```

The mock dispatches by SOAP method name, replays the captured response for that method, and models
the instrument lock — motion and transfer RPCs issued without holding the lock get the Echo's real
`Caller does not own the lock` fault, so the locking workflow can be tested deterministically.

## Live validation

The opt-in live tests from the [Echo](echo) page work against a 525 as well; set the expected model
so the identity check passes:

```bash
PYLABROBOT_ECHO_HOST=192.168.0.25 \
PYLABROBOT_ECHO_EXPECTED_MODEL="Echo 525" \
  uv run --extra dev pytest pylabrobot/labcyte/echo_live_tests.py
```
