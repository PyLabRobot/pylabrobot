# ODTC (On-Deck Thermocycler) Implementation Guide

## Overview

Interface for Inheco ODTC thermocyclers via SiLA (SOAP over HTTP). Asynchronous method execution (blocking and non-blocking), round-trip through **ODTC types** (ODTC XML ↔ ODTCProtocol / ODTCStep / ODTCStage), parallel commands (e.g. read temperatures during run), and **progress from DataEvents** via a single type: **ODTCProgress**.

- **Primary API:** Run protocols by name: `run_stored_protocol(name)`. Protocol is already on the device; no editing.
- **Secondary:** (1) **Edited ODTCProtocol** — get from device, change only **hold times and cycle count**; upload and run by name. Do not change temperature setpoints (overshoots are temperature- and ramp-specific). (2) **Protocol + ODTCConfig** — custom run: `protocol_to_odtc_protocol(protocol, config=get_default_config())`, then `run_protocol(odtc, block_max_volume)`.

**Architecture:** `ODTCSiLAInterface` (SiLA SOAP, state machine; stores raw DataEvent payloads) → `ODTCBackend` (method execution, protocol conversion; builds **ODTCProgress** from latest payload + protocol) → `ODTCThermocycler` (resource; preferred) or generic `Thermocycler` with `ODTCBackend`. Types in `odtc_model.py`.

**Progress:** One type — **ODTCProgress**. Built from raw DataEvent payload + optional protocol via `ODTCProgress.from_data_event(payload, odtc)`. Provides elapsed_s, temperatures, step/cycle/hold (from protocol when registered), and **estimated_duration_s** / **remaining_duration_s** (we compute these; device does not send them). Use `get_progress_snapshot()`, `get_hold_time()`, `get_current_step_index()`, `get_current_cycle_index()`; callback: `ODTCBackend(..., progress_callback=...)` receives ODTCProgress.

**Tutorial:** `odtc_tutorial.ipynb`. Sections: **Setup** → **Workflows** → **Types and conversion** → **Commands** → **Device protocols** → **DataEvents and progress** → **Error handling** → **Best practices** → **Complete example**.

## Setup

**Preferred: ODTCThermocycler** (owns dimensions and backend):

```python
from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling.inheco import ODTCThermocycler

tc = ODTCThermocycler(
    name="odtc",
    odtc_ip="192.168.1.100",
    variant=96,  # or 384; or 960000 / 384000
    child_location=Coordinate(0, 0, 0),
)
await tc.setup()  # HTTP event receiver + Reset + Initialize → idle
```

**Alternative:** Generic `Thermocycler` with `ODTCBackend(odtc_ip=..., variant=...)` for custom dimensions.

**Duration:** Device does not return duration. We set **estimated_duration_s** (PreMethod = 10 min; Method = from protocol or device; fallback = effective lifetime). **remaining_duration_s** = max(0, estimated_duration_s - elapsed_s). Used for `handle.estimated_remaining_time` and progress.

**Options:** `setup(full=True, simulation_mode=False, max_attempts=3, retry_backoff_base_seconds=1.0)`. Use `full=False` to only start the event receiver (e.g. **Reconnecting after session loss**).

**Simulation:** `await tc.backend.reset(simulation_mode=True)`; exit with `simulation_mode=False`. Commands return immediately with estimated duration.

**Reconnecting after session loss:** If the connection was lost while a method is running, create a new backend/thermocycler and call `await tc.backend.setup(full=False)` (do not full setup—that would Reset and abort). Use `wait_for_completion_by_time(...)` or a handle's `wait_resumable()` to wait; then `setup(full=True)` if needed for later commands.

**Cleanup:** `await tc.stop()`.

## Workflows

### 1. Run stored protocol by name (primary)

Protocol is already on the device; single call, no upload. Preferred usage.

**PreMethod before protocol:** You must run a **preMethod** (set block/mount temperature) **before** running a protocol by name. The block and lid temperatures from `set_block_temperature(...)` must **match** the protocol’s initial temperatures (e.g. the method’s `start_block_temperature` and initial lid temp). Run `set_block_temperature` to reach those temps, wait for completion, then call `run_stored_protocol(name)`.

```python
protocol_list = await tc.backend.list_protocols()
# Optional: get protocol to read initial temps for preMethod
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is not None:
    # PreMethod: match block/lid to protocol initial temps; wait for completion then run
    await tc.set_block_temperature(
        [odtc.start_block_temperature],
        lid_temperature=odtc.start_lid_temperature if odtc.start_lid_temperature else None,
        wait=True,
    )
execution = await tc.run_stored_protocol("PCR_30cycles")  # returns handle (wait=False default)
await execution  # block until done; or use wait=True to block on the call
```

### 2. Edited ODTCProtocol (secondary)

Get from device → modify **only hold times and cycle count** → upload → run. Preserves ODTC parameters (overshoot, slopes) because temperatures are unchanged.

**Avoid modifying temperature parameters** (e.g. `plateau_temperature`) on device-derived protocols: overshoots are temperature-difference and ramp-speed specific, so the device’s tuning no longer matches and thermal performance can suffer.

```python
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is None:
    raise ValueError("Protocol not found")
odtc.steps[1].plateau_time = 45.0   # hold time (s) — safe
# odtc.steps[4].loop_number = 35    # cycle count (adjust step to match protocol) — safe
# Do NOT change odtc.steps[i].plateau_temperature (overshoot/tuning is temperature-specific)
await tc.backend.upload_protocol(odtc, name="PCR_35cycles")
await tc.run_stored_protocol("PCR_35cycles")
```

For cycle count, set `loop_number` on the step that defines the cycle (the one with `goto_number`). Alternatively use **Workflow 4** to edit in Protocol form (`stage.repeats`).

### 3. Set block and lid temperature (preMethod)

Hold block (and lid) at a set temperature; ODTC runs a **PreMethod** (no direct SetBlockTemperature command). Run this **before** a protocol by name so block and lid match the protocol’s initial temperatures; then run the protocol. Default lid 110°C (96-well) or 115°C (384-well).

```python
# Returns handle by default (wait=False); await it to block
await tc.set_block_temperature([95.0])  # or: h = await tc.set_block_temperature([95.0]); await h
await tc.set_block_temperature([37.0], lid_temperature=110.0)
# Block on call: await tc.set_block_temperature([95.0], wait=True)
```

Estimated duration for this path is 10 minutes.

### 4. Custom run (Protocol + generic ODTCConfig) (secondary)

When you have a generic **Protocol** (e.g. from a builder): attach a generic **ODTCConfig** (e.g. `get_default_config()`), convert to ODTCProtocol, run. New protocols use default overshoot; for best thermal performance prefer running stored protocols by name (Workflow 1) or edited ODTCProtocol with only non-temperature changes (Workflow 2).

```python
from pylabrobot.thermocycling.inheco.odtc_model import protocol_to_odtc_protocol
from pylabrobot.thermocycling.standard import Protocol, Stage, Step

config = tc.backend.get_default_config(block_max_volume=50.0)
protocol = Protocol(stages=[Stage(steps=[Step(temperature=[95.0], hold_seconds=30.0)], repeats=30)])
odtc = protocol_to_odtc_protocol(protocol, config=config)
await tc.run_protocol(odtc, block_max_volume=50.0)
```

### 5. From XML file

`method_set = parse_method_set_file("my_methods.xml")` (from `odtc_model`), then `await tc.backend.upload_method_set(method_set)` and `await tc.run_stored_protocol("PCR_30cycles")`.

## Types and conversion

### ODTC types

| Type | Role |
|------|------|
| **ODTCStep** | Single temperature step: `plateau_temperature`, `plateau_time`, `slope`, overshoot, `goto_number`, `loop_number`. |
| **ODTCStage** | `steps: List[ODTCStep]`, optional `inner_stages`. |
| **ODTCProtocol** | **Methods** (cycling) and **premethods** (hold block/lid); `kind='method'` or `'premethod'`. For methods, **`.steps`** is the main representation (flat list with goto/loop). |
| **ODTCProgress** | Single progress type: built from raw DataEvent payload + optional protocol via `ODTCProgress.from_data_event(payload, odtc)`. Elapsed, temps, step/cycle/hold (when protocol registered), **estimated_duration_s** and **remaining_duration_s** (we compute; device does not send). Returned by `get_progress_snapshot()` and passed to **progress_callback**. |

When editing a device-derived ODTCProtocol (secondary usage), change only **hold times** (`plateau_time`) and **cycle count** (`loop_number`). Avoid changing temperature setpoints: overshoots are temperature-difference and ramp-speed specific.

### Protocol + ODTCConfig (custom runs only)

Use when you have a **Protocol** and want to run it on ODTC. **`odtc_method_to_protocol(odtc)`** returns `(Protocol, ODTCConfig)`; **`protocol_to_odtc_protocol(protocol, config=config)`** converts back. Conversion is **lossless** when you keep the same `ODTCConfig`.

**What ODTCConfig preserves:** Method-level: `name`, `fluid_quantity`, `variant`, `lid_temperature`, `post_heating`, `pid_set`, etc. Per-step (`config.step_settings[step_index]`): `slope`, overshoot (`overshoot_temperature`, `overshoot_time`, `overshoot_slope1`/`overshoot_slope2`), `lid_temp`, `pid_number`. **Protocol** holds temperatures, hold times, stage structure, repeat counts.

**Overshoot:** ODTC-specific; not in generic Protocol. Overshoots are **temperature-difference and ramp-speed specific** — tuning is valid for the setpoints it was designed for. Preserved in `ODTCConfig.step_settings`. When editing device-derived protocols, avoid changing temperatures; when building new protocols (Protocol + generic config), default overshoot applies.

**Conversion summary:** Device → ODTCProtocol: `get_protocol(name)`. ODTCProtocol → Protocol view: `odtc_protocol_to_protocol(odtc)` or `odtc_method_to_protocol(odtc)` for editing then `protocol_to_odtc_protocol(protocol, config=config)` and upload. Protocol + ODTCConfig → ODTCProtocol: `protocol_to_odtc_protocol(protocol, config=config)`.

**Method name:** Device identifies protocols by string (e.g. `"PCR_30cycles"`, `"plr_currentProtocol"`). Use with `run_stored_protocol(name)`, `get_protocol(name)`, `list_protocols()`.

**API:** `tc.run_stored_protocol(name)`, `tc.run_protocol(odtc, block_max_volume)` (ODTCProtocol or Protocol). Backend: `list_protocols()`, `get_protocol(name)` → `Optional[ODTCProtocol]`, `upload_protocol(protocol_or_odtc, name=..., config=...)` (config only when protocol is `Protocol`), `set_block_temperature(...)`, `get_default_config()`, `execute_method(method_name)`.

## Commands and execution

**Default: async.** Execution commands (**run_stored_protocol**, **set_block_temperature**, **run_protocol**) default to **wait=False**: they return an execution handle (async). Pass **wait=True** to block until completion. So all such commands are async unless you specify otherwise.

**Synchronous** (no wait parameter; complete before returning): **setup()**, **get_status()**, **get_device_identification()**, **read_temperatures()**, **list_protocols()**, **get_protocol()**, and other informational calls.

**Lid/door:** **open_lid** and **close_lid** default to **wait=True** (block); pass **wait=False** to get a handle.

**Example:**

```python
# run_stored_protocol and set_block_temperature default to wait=False → handle
execution = await tc.run_stored_protocol("PCR_30cycles")
temps = await tc.read_temperatures()  # parallel where allowed
if await execution.is_running():
    print(f"Method {execution.method_name} still running")
events = await execution.get_data_events()
await execution  # block until done

# To block on start: execution = await tc.run_stored_protocol("PCR_30cycles", wait=True)
# Lid: door_opening = await tc.open_lid(wait=False); await door_opening
```

**State:** `await tc.is_profile_running()`. `await tc.wait_for_profile_completion(poll_interval=5.0, timeout=3600.0)`.

**Temperature:** `await tc.set_block_temperature([temp])` or with `lid_temperature=...`; returns handle by default (wait=False). Implemented via PreMethod (no direct SetBlockTemperature).

**Parallel with ExecuteMethod:** ✅ ReadActualTemperature, OpenDoor/CloseDoor, StopMethod. ❌ SetParameters/GetParameters, GetLastData, another ExecuteMethod.

**Waiting:** Await a handle (`await execution`) or use `wait=True`. Backend polls latest DataEvent at **progress_log_interval** (default 150 s), builds **ODTCProgress**, and logs it (and/or calls **progress_callback** with ODTCProgress).

**Execution handle (ODTCExecution):** `request_id`, `command_name`, `estimated_remaining_time` (our estimate when protocol known; else effective lifetime); awaitable, `wait()`, `get_data_events()`. ExecuteMethod: `method_name`, `is_running()`, `stop()`.

## Device protocols

**List:** `protocol_list = await tc.backend.list_protocols()` (ProtocolList: `.methods`, `.premethods`, `.all`). Or `methods, premethods = await tc.backend.list_methods()`.

**Get by name:** `odtc = await tc.backend.get_protocol("PCR_30cycles")` → `Optional[ODTCProtocol]` (None for premethods or missing). Then modify and upload (Workflow 2) or `print(odtc)` and run by name.

**Full MethodSet (advanced):** `method_set = await tc.backend.get_method_set()` → ODTCMethodSet; iterate `method_set.methods` and `method_set.premethods`.

**Display:** `print(odtc)` and `print(await tc.backend.read_temperatures())` show labeled summaries. When you await a handle, INFO logs multi-line command/duration/remaining.

## DataEvents and progress

During method execution the device sends **DataEvent** messages (raw payloads). We store them and turn the latest into **ODTCProgress** in one place: **`ODTCProgress.from_data_event(payload, odtc)`**. The device sends **elapsed time and temperatures** (block/lid) only; it does **not** send step/cycle/hold or estimated/remaining duration — we derive those from the protocol when it is registered.

**ODTCProgress** (single type): `elapsed_s`, `current_temp_c`, `target_temp_c`, `lid_temp_c`; when protocol is registered: `current_step_index`, `total_step_count`, `current_cycle_index`, `total_cycle_count`, `remaining_hold_s`, **`estimated_duration_s`** (protocol total), **`remaining_duration_s`** = max(0, estimated_duration_s - elapsed_s). Use **`get_progress_snapshot()`** → ODTCProgress; **`get_hold_time()`**, **`get_current_step_index()`**, **`get_current_cycle_index()`** read from the same snapshot. **`progress_callback`** (if set) receives ODTCProgress every **progress_log_interval** (default 150 s). Set `progress_log_interval` to `None` or `0` to disable logging/callback.

**Logging:** When you await a handle, the backend logs progress (e.g. `progress.format_progress_log_message()`) every progress_log_interval. Configure `pylabrobot.thermocycling.inheco` (and optionally `pylabrobot.storage.inheco`) for level. Optional raw DataEvent JSONL: **`tc.backend.data_event_log_path`** = file path.

**ExecuteMethod:** Backend waits for the first DataEvent (up to `first_event_timeout_seconds`, default 60 s) to set handle lifetime/ETA from our estimated duration. Completion is via ResponseEvent or GetStatus polling.

## Error handling

**Return codes:** 1 = sync success; 2 = async accepted; 3 = async completed (ResponseEvent); 4 = device busy; 5 = LockId mismatch; 6 = invalid/duplicate requestId; 9 = command not allowed in current state.

**State transitions:** `startup` → `standby` (Reset) → `idle` (Initialize) → `busy` (async command) → `idle` (completion).

## Best practices

1. **Always call `setup()`** before using the device.
2. **Async by default:** run_stored_protocol and set_block_temperature default to wait=False (return handle); use wait=True to block when you need to wait before continuing.
3. **Check state** with `is_profile_running()` before starting new methods.
4. **Prefer running stored protocols by name** (primary). **Run a preMethod first:** use `set_block_temperature(...)` so block and lid match the protocol’s initial temperatures (`start_block_temperature`, `start_lid_temperature`), then run the protocol. Secondary: edited ODTCProtocol (change only hold times and cycle count; **avoid changing temperatures** — overshoots are temperature- and ramp-specific) or Protocol + generic config for custom runs. When using Protocol + device-derived config, preserve the `ODTCConfig` from `odtc_method_to_protocol(odtc)`. New protocols (generic Protocol) use `get_default_config()`; overshoot defaults until automatic derivation (future work).
5. **Handle timeouts** when waiting for method completion.
6. **Clean up** with `stop()` when done.

## Complete example

```python
from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling.inheco import ODTCThermocycler
from pylabrobot.thermocycling.inheco.odtc_model import protocol_to_odtc_protocol
from pylabrobot.thermocycling.standard import Protocol, Stage, Step

tc = ODTCThermocycler(name="odtc", odtc_ip="192.168.1.100", variant=96, child_location=Coordinate(0, 0, 0))
await tc.setup()

# Run modified ODTCProtocol without saving a new template (run_protocol uploads to scratch, runs, no overwrite of stored methods)
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is not None:
    odtc.steps[1].plateau_time = 45.0
    execution = await tc.run_protocol(odtc, block_max_volume=50.0)
    await execution

# Custom run: Protocol + ODTCConfig
config = tc.backend.get_default_config(block_max_volume=50.0)
protocol = Protocol(stages=[
    Stage(steps=[
        Step(temperature=[95.0], hold_seconds=30.0),
        Step(temperature=[60.0], hold_seconds=30.0),
        Step(temperature=[72.0], hold_seconds=60.0),
    ], repeats=30)
])
odtc = protocol_to_odtc_protocol(protocol, config=config)
await tc.run_protocol(odtc, block_max_volume=50.0)

await tc.set_block_temperature([37.0])
await tc.stop()
```
