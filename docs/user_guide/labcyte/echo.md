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
- upload source plate maps with `set_plate_map()`
- run `PlateSurvey`, retrieve `GetSurveyData`, and run `DryPlate`

The driver matches the Echo's observed transport quirks:

- HTTP-like `POST /Medman`
- gzip-compressed SOAP bodies
- LF-terminated request headers
- token-based client identity reused for lock ownership

State normalization notes:

- source access uses the explicit `LSO` / `LSI` bits when present and falls back to `SPP`
- destination access is currently inferred from `DPP`
- door state uses `DFO` / `DFC` when present and otherwise falls back to the normalized access
  state

Survey notes:

- survey support is Echo-specific for now and lives in `pylabrobot.labcyte`
- the validated survey workflow is `SetPlateMap -> PlateSurvey`, with optional
  `GetSurveyData` and `DryPlate(TWO_PASS)`
- retracting a loaded source plate is much slower than an empty retract; the
  Echo integration uses longer default timeouts when `plate_type` is supplied

## Safe Source Access Cycle

```python
import asyncio

from pylabrobot.labcyte import Echo


async def main():
  async with Echo(host="192.168.0.25") as echo:
    await echo.lock()
    try:
      opened = await echo.open_source_plate(timeout=2.0)
      print(opened.raw)

      retracted = await echo.close_source_plate(timeout=2.0)
      print(retracted.raw)

      closed = await echo.close_door(timeout=2.0)
      print(closed.raw)
    finally:
      await echo.unlock()


asyncio.run(main())
```

## Surveying a Source Plate

```python
import asyncio

from pylabrobot.labcyte import Echo, EchoPlateMap, EchoSurveyParams

async def main():
  async with Echo(host="192.168.0.25") as echo:
    await echo.lock()
    try:
      plate_map = EchoPlateMap(
        plate_type="384PP_DMSO2",
        well_identifiers=("A1", "A2", "B1", "B2"),
      )
      result = await echo.survey_source_plate(
        plate_map,
        EchoSurveyParams(
          plate_type="384PP_DMSO2",
          num_rows=16,
          num_cols=24,
        ),
        fetch_saved_data=True,
        dry_after=True,
      )
      print(result.saved_data.wells[0].identifier if result.saved_data else "no saved data")
    finally:
      await echo.unlock()


asyncio.run(main())
```

`survey_source_plate()` does not change access state for you. It assumes the
plate is already loaded, retracted, and ready for survey.

## Scope

This integration does not yet implement:

- event stream registration on port `8010`
- transfer planning or `DoWellTransfer`
- survey uploads via `SetSurveyData`
