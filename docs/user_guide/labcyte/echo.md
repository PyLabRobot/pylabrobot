# Echo

The Labcyte Echo integration currently targets the Medman surface validated against an Echo 650.
It covers safe mechanical access operations, source-plate survey workflows, PLR-native transfer
planning, and raw Echo protocol execution through `DoWellTransfer`.

Supported operations:

- fetch instrument identity with `get_instrument_info()`
- fetch Echo configuration, power calibration, protocol names, protocol payloads, and fluid metadata
- lock and unlock the instrument session
- poll `GetDIOEx2` through `get_access_state()`
- check registered source and destination plate presence
- present and retract the source-side access path
- present and retract the destination-side access path
- open and close the door
- home all axes
- control the coupling-fluid pump direction, bubbler pump, and bubbler nozzle
- control the vacuum pump/nozzle and ionizer
- upload source plate maps with `set_plate_map()`
- run `PlateSurvey`, retrieve `GetSurveyData`, and run `DryPlate`
- build Echo protocol XML from PLR source wells, destination wells, and volumes
- execute an existing Echo transfer protocol XML with `do_well_transfer()`
- execute PLR-native transfers with `transfer_wells()`
- parse transfer reports into completed and skipped wells
- update PLR volume trackers from survey and transfer results when volume tracking is enabled
- run source/destination load and eject workflows with caller-provided operator pauses

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

Transfer notes:

- `do_well_transfer()` is a thin wrapper around the Echo `DoWellTransfer` RPC
- `transfer_wells()` builds the Echo protocol XML from PLR wells and executes it
- `EchoTransferPrintOptions` controls the nested `PrintOptions` payload
- Echo transfer volumes are in nL by default; pass `volume_unit="uL"` to use PLR-style uL inputs
- successful transfer reports update PLR volume trackers only when PLR volume tracking is enabled

## Low-Level Metadata And Controls

```python
import asyncio

from pylabrobot.labcyte import Echo

async def main():
  async with Echo(host="192.168.0.25") as echo:
    info = await echo.get_instrument_info()
    config_xml = await echo.get_echo_configuration()
    fluid = await echo.get_fluid_info("DMSO")
    protocols = await echo.get_all_protocol_names()
    print(info.model, len(config_xml), fluid.name, protocols)

    await echo.lock()
    try:
      await echo.open_door()
      await echo.home_axes()
      await echo.set_pump_direction(True)
      await echo.enable_bubbler_pump(True)
      await echo.actuate_bubbler_nozzle(True)
      await echo.enable_vacuum_nozzle(True)
      await echo.actuate_ionizer(True)
    finally:
      await echo.unlock()

asyncio.run(main())
```

The actuator calls are low-level Echo RPCs. They require an instrument lock and should be validated
on the target Echo 650 before they are used in an automated workflow.

## Loading And Ejecting Plates

```python
import asyncio

from pylabrobot.labcyte import Echo

async def pause(message: str):
  input(f"{message}. Press Enter when ready.")

async def main():
  async with Echo(host="192.168.0.25") as echo:
    await echo.lock()
    try:
      loaded = await echo.load_source_plate(
        "384PP_DMSO2",
        operator_pause=pause,
      )
      print(loaded.plate_present, loaded.barcode)

      await echo.eject_source_plate(operator_pause=pause, open_door_first=True)
    finally:
      await echo.unlock()

asyncio.run(main())
```

The operator pause callback is optional. It is where an application should prompt the user to place
or remove the plate after the gripper has been presented.

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

## Running an Existing Echo Transfer Protocol

```python
import asyncio

from pylabrobot.labcyte import Echo, EchoTransferPrintOptions

PROTOCOL_XML = """
<Protocol>
  <Name>example</Name>
  <!-- Existing Echo protocol XML goes here. -->
</Protocol>
"""

async def main():
  async with Echo(host="192.168.0.25") as echo:
    await echo.lock()
    try:
      result = await echo.do_well_transfer(
        PROTOCOL_XML,
        EchoTransferPrintOptions(
          do_plate_survey=True,
          monitor_power=True,
          save_print=True,
          plate_map=True,
        ),
        timeout=300.0,
      )
      print(result.report_xml or result.raw)
    finally:
      await echo.unlock()


asyncio.run(main())
```

`do_well_transfer()` intentionally does not synthesize a protocol from PLR resources. Use it when
you already have a valid Echo transfer protocol XML document and want PyLabRobot to execute it
through Medman.

## Running PLR-Native Transfers

```python
import asyncio

from pylabrobot.labcyte import Echo
from pylabrobot.resources import set_volume_tracking

async def main(source_plate, destination_plate):
  set_volume_tracking(True)
  source_plate.get_well("A1").set_volume(1.0)  # PLR trackers use uL.

  async with Echo(host="192.168.0.25") as echo:
    await echo.lock()
    try:
      result = await echo.transfer_wells(
        source_plate,
        destination_plate,
        [("A1", "B1", 5.0)],  # Echo-native nL by default.
        source_plate_type="384PP_DMSO2",
        destination_plate_type="1536LDV_Dest",
        do_survey=True,
        timeout=300.0,
      )
      print(len(result.transfers), len(result.skipped))
    finally:
      await echo.unlock()
```

`transfer_wells()` performs the observed Echo flow: source/destination checks, sparse source
`SetPlateMap`, optional source survey, status reads, protocol XML generation, `DoWellTransfer`, and
structured report parsing. When volume tracking is enabled, survey data can set measured source
volumes and successful transfer report entries move actual dispensed volume from source wells to
destination wells.

## Scope

This integration does not yet implement:

- live validation of the new low-level actuator and workflow calls on every Echo 650 firmware build
- a CSV picklist parser or UI layer
