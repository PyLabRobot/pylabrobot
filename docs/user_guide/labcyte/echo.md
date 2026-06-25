# Echo

The Labcyte Echo integration currently targets the Medman surface validated against an Echo 650.
It covers safe mechanical access operations, source-plate survey workflows, PLR-native transfer
planning, and raw Echo protocol execution through `DoWellTransfer`.

```{note}
Using an **Echo 525**? It speaks the same Medman protocol as the 650 and reuses everything on this
page. The only behavioural difference is its coarser 25 nL transfer increment. See
[Echo 525](echo-525) for the `model="Echo 525"` selector and the hardware-free mock server.
```

Supported operations:

- fetch instrument identity with `get_instrument_info()`
- fetch Echo configuration, power calibration, protocol names, protocol payloads, and fluid metadata
- fetch typed power calibration, focus time-of-flight, scan-position, fluid, and plate-insert
  metadata
- fetch typed Echo source/destination plate catalogs with `get_echo_plate_catalog()`
- reconcile PLR plates against Echo-defined plate types with `resolve_echo_plate_type()`
- clone/delete destination plate definitions through the direct Echo `SetPlateInfoEx` /
  `RemovePlateInfo` API
- lock and unlock the instrument session
- poll `GetDIOEx2` through `get_access_state()`
- check registered source and destination plate presence
- present and retract the source-side access path
- present and retract the destination-side access path
- open and close the door
- home all axes
- control the coupling-fluid pump direction, bubbler pump, and bubbler nozzle
- control the vacuum pump/nozzle and ionizer
- read and set Echo focus time-of-flight values, and expose low-level power/scanner calibration
  RPCs
- upload source plate maps with `set_plate_map()`
- run `PlateSurvey`, retrieve `GetSurveyData`, and run `DryPlate`
- build Echo protocol XML from PLR source wells, destination wells, and volumes
- execute an existing Echo transfer protocol XML with `do_well_transfer()`
- execute PLR-native transfers with `transfer()` or `transfer_wells()`
- parse transfer reports into completed and skipped wells
- update PLR volume trackers from survey and transfer results when volume tracking is enabled
- run source/destination load and eject workflows with caller-provided operator pauses
- model the physical Echo source and destination positions as PLR plate holders

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
- `transfer()` accepts PLR wells directly, infers the source/destination plates, and executes them
- `transfer_wells()` builds the Echo protocol XML from explicit plates plus well references and
  executes it
- `EchoTransferPrintOptions` controls the nested `PrintOptions` payload
- Echo transfer volumes are in nL by default; pass `volume_unit="uL"` to use PLR-style uL inputs
- successful transfer reports update PLR volume trackers only when PLR volume tracking is enabled

Plate catalog notes:

- the Echo instrument catalog is authoritative for Echo protocol plate type names
- if `source_plate_type` or `destination_plate_type` is omitted, PLR uses the plate resource's
  `model` only when that exact name exists in the relevant Echo catalog
- transfer and load workflows fail before motion or mutation when the Echo does not know the
  requested plate type
- PLR validates Echo `Rows` / `Columns` against the PLR plate grid before transfer planning
- source survey dimensions use Echo-reported columns, while PLR wells and volume trackers remain the
  source of transfer intent and local state

## Architecture

The integration follows the PLR device/driver/capability split:

- `Echo` is the user-facing device frontend. It exposes Echo operations, owns the source and
  destination PLR plate holders, and delegates instrument I/O to its driver.
- `EchoDriver` owns the Medman protocol details: SOAP envelope construction, gzip framing,
  lock tokens, polling, event streams, survey parsing, and transfer report parsing.
- `EchoPlateAccessBackend` adapts the Echo driver to the generic `PlateAccess` capability.
- Application code, web services, and workcell controllers should call the `Echo` frontend or the
  `PlateAccess` capability instead of duplicating Medman transport logic.

## Live Echo 650 Validation

The non-live unit tests mock the Medman transport. Before relying on a new Echo firmware build or
new workflow code, run the opt-in live validation tests against an idle Echo 650:

```bash
PYLABROBOT_ECHO_HOST=192.168.0.25 \
  uv run --extra dev pytest pylabrobot/labcyte/echo_live_tests.py
```

By default, the live validation reads identity, configuration, access state, plate catalogs,
protocol catalogs, and verifies that the event channel can be opened. It does not move plates,
grippers, or the door.

To include a door open/close lock cycle, run the same test with explicit motion enabled:

```bash
PYLABROBOT_ECHO_HOST=192.168.0.25 \
PYLABROBOT_ECHO_VALIDATE_MOTION=1 \
  uv run --extra dev pytest pylabrobot/labcyte/echo_live_tests.py
```

The expected model defaults to `Echo 650`. Override it only when validating a compatible
instrument variant:

```bash
PYLABROBOT_ECHO_EXPECTED_MODEL="Echo 655"
```

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
      result = await echo.transfer(
        [(source_plate.get_well("A1"), destination_plate.get_well("B1"), 5.0)],
        source_plate_type="384PP_DMSO2",
        destination_plate_type="1536LDV_Dest",
        do_survey=True,
        timeout=300.0,
      )
      print(len(result.transfers), len(result.skipped))
    finally:
      await echo.unlock()
```

The `Echo` frontend also exposes the physical plate positions as PLR resource holders:

```python
echo.source_plate = source_plate
echo.destination_plate = destination_plate

assert echo.source_plate is source_plate
assert source_plate.parent is echo.source_position
```

Use these positions to keep a PLR workcell model aligned with the plates physically loaded in the
Echo. The holders accept `Plate` resources only; source/destination access commands still control
the real instrument state.

`transfer()` is the high-level PLR API. It accepts `Well` objects, infers one source plate and one
destination plate from their parents, and then uses the same execution path as `transfer_wells()`.
Use `transfer_wells()` when the caller has plate objects plus string well identifiers. Both methods
perform the observed Echo flow: source/destination checks, sparse source `SetPlateMap`, optional
source survey, status reads, protocol XML generation, `DoWellTransfer`, and structured report
parsing. When volume tracking is enabled, survey data can set measured source volumes and successful
transfer report entries move actual dispensed volume from source wells to destination wells.

## Focus And Calibration

PyEcho covers the same Medman transport and most normal operation calls, but its Focus tab is not
implemented. The PLR Echo driver includes the Focus/Calibration RPCs decoded from the installed Echo
client binaries and verified with packet capture on `echo-win`.

Read-side helpers:

- `get_echo_power_calibration()` parses `GetPwrCal` into typed amplitude, reference-energy,
  feedback, and system-gain values
- `get_focus_tof()` / `get_duo_focus_tof()` read `GetTOFFocus` / `GetDuoTOFFocus`
- `get_coupling_fluid_sound_velocity()` reads `GetCouplingFluidSoundVelocity`
- `get_scan_positions()` reads `GetScanPositions`
- `get_calibration_plate_names()` reads `GetCalPlateNames`
- `get_focus_state()` gathers the focus, scan-position, sound-velocity, and power-calibration
  reads into one state object

Low-level calibration controls:

- `set_focus_tof()` and `set_duo_focus_tof()` send Echo's string-valued numeric focus setters
- `calibrate_power()` and `commit_power_calibration()` expose `CalibratePower` / `CommitPwrCal`
- `retract_source_gripper_for_scan_calibration()` and
  `retract_destination_gripper_for_scan_calibration()` expose the scan-calibration retract paths
- `calibrate_scanner()` and `cancel_scanner_calibration()` expose scanner calibration control
- `focal_sweep()` exposes the low-level `FocalSweep` RPC

The read-side calls and no-op focus setter shape were live-verified. The calibration calls are
deliberately low-level and require an instrument lock because they can move hardware or change
calibration state.

Additional catalog helpers decoded during the same pass:

- `get_dio_ex()` for raw `GetDIOEx`
- `get_all_fluid_types()` and `get_fluids_for_plate()`
- `get_all_plate_inserts()`
- `get_transfer_volume_resolution_nl()`

## Echo Plate Definitions

PLR reads the source and destination plate definitions already registered on the Echo. It can create
a minimal transfer-compatible `Plate` from `EchoPlateInfo` with `create_plate_from_echo_info()`, but
that helper is not a manufacturer-precise labware definition.

PLR can clone/delete destination plate definitions through the direct Echo Medman API, without Echo
Client Utility or vendor DLLs:

- `clone_destination_plate_definition(base_plate_type, new_plate_type)` reads the existing
  destination definition, sends the captured `SetPlateInfoEx` payload shape, and verifies the new
  name appears in the destination catalog
- `delete_destination_plate_definition(plate_type)` sends `RemovePlateInfo` and verifies the name
  leaves the destination catalog
- `set_plate_info_ex()` and `remove_plate_info()` remain low-level escape hatches

Source plate definition writes are not exposed by PLR. In testing, the Echo Client Utility write
surface sent `SetPlateInfoEx`, accepted a cloned source definition, but registered it in the
destination catalog even when the source usage fields were preserved. Treat source definitions as
read-only from PLR for now.

No pcapng capture is needed for catalog reconciliation, validation, transfer, survey, loading
existing Echo-defined plates, or cloning/deleting destination definitions. The destination write path
was decoded from an Echo Client Utility capture and verified by sending `SetPlateInfoEx` /
`RemovePlateInfo` directly to the instrument. A future capture is still useful if source
plate-definition writes need to be decoded beyond the observed `SetPlateInfoEx` behavior.

## Scope

This integration does not yet implement:

- live validation of every low-level actuator and workflow call on every Echo 650 firmware build
- a CSV picklist parser or UI layer
- arbitrary plate-definition editing beyond cloning an existing destination definition
- source plate-definition writes
