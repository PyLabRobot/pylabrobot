# ODTC (On-Deck Thermocycler) Implementation Guide

## Overview

Interface for Inheco ODTC thermocyclers via SiLA (SOAP over HTTP). Supports asynchronous method execution (blocking and non-blocking), round-trip protocol conversion (ODTC XML ↔ PyLabRobot `Protocol` with lossless ODTC parameters), parallel commands (e.g. read temperatures during run), and DataEvent collection.

**New users:** Start with **Connection and Setup**, then **ODTC Model** (types and conversion), then **Recommended Workflows** (run by name, round-trip for thermal performance, set block/lid temp). A step-by-step tutorial notebook is in **`odtc_tutorial.ipynb`**. Use **Running Commands** and **Getting Protocols** for async handles; **ODTCProtocol and Protocol + ODTCConfig Conversion** for conversion detail.

## Architecture

- **`ODTCSiLAInterface`** (`odtc_sila_interface.py`) — SiLA SOAP layer: `send_command` / `start_command`, parallelism rules, state machine (Startup → Standby → Idle → Busy), lockId, DataEvents.
- **`ODTCBackend`** (`odtc_backend.py`) — Implements `ThermocyclerBackend`: method execution, protocol conversion, upload/download, status.
- **`ODTCThermocycler`** (`odtc_thermocycler.py`) — Preferred resource: takes `odtc_ip`, `variant` (96/384 or 960000/384000), uses ODTC dimensions (147×298×130 mm). Alternative: generic `Thermocycler` with `ODTCBackend` for custom sizing.
- **`odtc_model.py`** — MethodSet XML (de)serialization, `ODTCProtocol` ↔ `Protocol` conversion, `ODTCConfig` for ODTC-specific parameters.

## ODTC Model: Types and Conversion

The ODTC implementation is built around **ODTCProtocol**, **ODTCStage**, and **ODTCStep**, which extend PyLabRobot’s generic **Protocol**, **Stage**, and **Step**. The device stores protocols by a **method name** (string); conversion functions map between ODTC types and the generic types for editing and round-trip.

### Core types

| Type | Role |
|------|------|
| **`ODTCStep`** | Extends `Step`. Single temperature step with ODTC fields (slope, overshoot, plateau_time, goto_number, loop_number). |
| **`ODTCStage`** | Extends `Stage`. Holds `steps: List[ODTCStep]` and optional `inner_stages` for nested loops. |
| **`ODTCProtocol`** | Extends `Protocol`. One type for both **methods** (cycling) and **premethods** (hold block/lid temp), distinguished by `kind='method'` or `kind='premethod'`. |

For **methods** (kind='method'): **`.steps`** is the main representation—a flat list of `ODTCStep` with step numbers and goto/loop. When built from a generic `Protocol` (e.g. `protocol_to_odtc_protocol`), we set `stages=[]`; the stage view is derived when needed via `odtc_protocol_to_protocol(odtc)` (which builds a `Protocol` with stages from the step list). Parsed XML with nested loops can produce an ODTCProtocol whose stage tree is built from steps for display or serialization.

### Generic types (PyLabRobot)

- **`Protocol`** — `stages: List[Stage]`; hardware-agnostic.
- **`Stage`** — `steps: List[Step]`, `repeats: int`.
- **`Step`** — `temperature: List[float]`, `hold_seconds: float`, optional `rate`.

Example: `Protocol(stages=[Stage(steps=[Step(temperature=[95.0], hold_seconds=30.0)], repeats=1)])`.

### Conversion

- **Device → editable (Protocol + ODTCConfig):**
  `get_protocol(name)` returns `Optional[ODTCProtocol]`. Use **`odtc_method_to_protocol(odtc)`** to get `(Protocol, ODTCConfig)` for modifying then re-uploading with the same thermal tuning.

- **Protocol + ODTCConfig → ODTC (upload/run):**
  Use **`protocol_to_odtc_protocol(protocol, config=config)`** to get an `ODTCProtocol` for upload or for passing to `run_protocol(odtc, block_max_volume)`.

- **ODTCProtocol → Protocol view only:**
  Use **`odtc_protocol_to_protocol(odtc)`** to get `(Protocol, ODTCProtocol)` when you need a generic Protocol view (e.g. stage tree) without a separate ODTCConfig.

### Method name (string)

The device identifies stored protocols by a **method name** (SiLA: `methodName`), e.g. `"PCR_30cycles"`, `"plr_currentProtocol"`. Use it with `run_stored_protocol(name)`, `get_protocol(name)`, and `list_protocols()`.

**API:** `tc.run_protocol(protocol, block_max_volume)` or `tc.run_stored_protocol(name)`. Backend: `list_protocols()`, `get_protocol(name)` → `Optional[ODTCProtocol]` (runnable methods only; premethods → `None`), `upload_protocol(protocol, name=..., config=...)`, `set_block_temperature(...)`, `get_default_config()`, `execute_method(method_name)`.

## Connection and Setup

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

**Alternative:** Generic `Thermocycler` with `ODTCBackend` (e.g. custom dimensions):

```python
from pylabrobot.thermocycling.inheco import ODTCBackend
from pylabrobot.thermocycling.thermocycler import Thermocycler

backend = ODTCBackend(odtc_ip="192.168.1.100", variant=960000)
tc = Thermocycler(name="odtc", size_x=147, size_y=298, size_z=130, backend=backend, child_location=Coordinate(0, 0, 0))
await tc.setup()
```

**Estimated duration:** The device does not return duration in the async response. We compute it: PreMethod = 10 min; Method = from steps (ramp + plateau + overshoot, with loops). This estimate is used for `handle.estimated_remaining_time`, when to start polling, and a tighter timeout cap.

**Setup options:** `setup(full=True, simulation_mode=False, max_attempts=3, retry_backoff_base_seconds=1.0)`. When `full=True` (default), the full path runs up to `max_attempts` times with exponential backoff on failure (e.g. flaky network). Use `max_attempts=1` to disable retry. Use `full=False` to only start the event receiver without resetting the device (see **Reconnecting after session loss** below).

### Simulation mode

Enter simulation mode: `await tc.backend.reset(simulation_mode=True)`. Exit: `await tc.backend.reset(simulation_mode=False)`. In simulation mode, commands return immediately with estimated duration; valid until the next Reset. Check state without resetting: `tc.backend.simulation_mode` reflects the last `reset(simulation_mode=...)` call. To bring the device up in simulation: `await tc.setup(simulation_mode=True)` (full path with simulation enabled).

### Reconnecting after session loss

If the session or connection was lost while a method is running, you can reconnect without aborting the method. Create a new backend (or thermocycler), then call `await tc.backend.setup(full=False)` to only start the event receiver—do **not** call full setup (that would Reset and abort the method). Then use `wait_for_completion_by_time(...)` or a persisted handle's `wait_resumable()` to wait for the in-flight method to complete. After the method is done, call `setup(full=True)` if you need a full session for subsequent commands.

### Cleanup

```python
await tc.stop()  # Closes HTTP server and connections
```

## Recommended Workflows

Use these patterns for the best balance of simplicity and thermal performance.

### 1. Run stored protocol by name

**Use when:** The protocol (method) is already on the device. Single instrument call; no upload.

```python
# List names: methods and premethods (ProtocolList with .methods, .premethods, .all)
protocol_list = await tc.backend.list_protocols()

# Run by name (blocking or non-blocking)
await tc.run_stored_protocol("PCR_30cycles")
# Or with handle: execution = await tc.run_stored_protocol("PCR_30cycles", wait=False); await execution
# When awaiting a handle, progress is logged every progress_log_interval (default 150 s) for method runs.
```

### 2. Get → modify → upload with config → run (round-trip for thermal performance)

**Use when:** You want to change an existing device protocol (e.g. cycle count) while keeping equivalent thermal performance. Preserving `ODTCConfig` keeps overshoot and other ODTC parameters from the original.

```python
from pylabrobot.thermocycling.inheco.odtc_model import odtc_method_to_protocol, protocol_to_odtc_protocol

# Get runnable protocol from device (returns None for premethods)
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is None:
    raise ValueError("Protocol not found")
protocol, config = odtc_method_to_protocol(odtc)

# Modify only durations or cycle counts; keep temperatures unchanged
# ODTCConfig is tuned for the original temperature setpoints—change temps and tuning may be wrong
protocol.stages[0].repeats = 35  # Safe: cycle count
# Do NOT change temperature setpoints when reusing config

# Upload with same config so overshoot/ODTC params are preserved
await tc.backend.upload_protocol(protocol, name="PCR_35cycles", config=config)

# Run by name
await tc.run_stored_protocol("PCR_35cycles")
```

**Why:** New protocols created without a config use default overshoot parameters and can heat more slowly. Using `get_protocol` + `upload_protocol(..., config=config)` preserves the device’s thermal tuning.

**Important:** When reusing an ODTC-specific config, **preserve temperature setpoints** (plateau temperatures, lid, etc.). The config's overshoot and ramp parameters are calibrated for those temperatures. Only **durations** (hold times) and **cycle/repeat counts** are safe to change—they don't affect thermal tuning. Changing target temperatures while keeping the same config can give suboptimal or inconsistent thermal performance.

### 3. Set block and lid temperature (PreMethod equivalent)

**Use when:** You want to hold the block (and lid) at a set temperature without running a full cycling method. ODTC implements this by uploading and running a PreMethod.

```python
# Block to 95°C with default lid (110°C for 96-well, 115°C for 384-well)
await tc.set_block_temperature([95.0])

# Custom lid temperature
await tc.set_block_temperature([37.0], lid_temperature=110.0)

# Non-blocking
execution = await tc.set_block_temperature([95.0], lid_temperature=110.0, wait=False)
await execution
```

ODTC has no direct SetBlockTemperature command; `set_block_temperature()` creates and runs a PreMethod internally. Estimated duration for this path is 10 minutes (see Connection and Setup).

## Running Commands

### Synchronous Commands

Some commands are synchronous and return immediately:

```python
# Get device status
status = await tc.get_status()  # Returns "idle", "busy", "standby", etc.

# Get device identification
device_info = await tc.get_device_identification()
```

### Asynchronous Commands

Most ODTC commands are asynchronous and support both blocking and non-blocking execution:

#### Blocking Execution (Default)

```python
# Block until command completes
await tc.open_lid()  # Returns None when complete
await tc.close_lid()
await tc.initialize()
await tc.reset()
```

#### Non-Blocking Execution with Handle

```python
# Start command and get execution handle
door_opening = await tc.open_lid(wait=False)
# Returns CommandExecution handle immediately

# Do other work while command runs
temps = await tc.read_temperatures()  # Can run in parallel if allowed

# Wait for completion
await door_opening  # Await the handle directly
# OR
await door_opening.wait()  # Explicit wait method
```

#### CommandExecution Handle

- **`request_id`**, **`command_name`**, **`estimated_remaining_time`** (seconds; from our computed estimate when available)
- **Awaitable** (`await handle`) and **`wait()`**
- **`get_data_events()`** — DataEvents for this execution

```python
# Non-blocking door operation
door_opening = await tc.open_lid(wait=False)

# Get DataEvents for this execution
events = await door_opening.get_data_events()

# Wait for completion
await door_opening
```

### Method Execution

- **Blocking:** `await tc.run_stored_protocol("PCR_30cycles")` or `await tc.run_protocol(protocol, block_max_volume=50.0)` (upload + execute).
- **Non-blocking:** `execution = await tc.run_stored_protocol("PCR_30cycles", wait=False)`; then `await execution` or `await execution.wait()` or `await tc.wait_for_method_completion()`.
- While a method runs you can call `read_temperatures()`, `open_lid(wait=False)`, etc. (parallel where allowed).

#### MethodExecution Handle

Extends `CommandExecution` with **`method_name`**, **`is_running()`** (device busy state), **`stop()`** (StopMethod).

```python
execution = await tc.run_stored_protocol("PCR_30cycles", wait=False)

# Check status
if await execution.is_running():
    print(f"Method {execution.method_name} still running (ID: {execution.request_id})")

# Get DataEvents for this execution
events = await execution.get_data_events()

# Wait for completion
await execution
```

### State Checking

```python
# Check if method is running
is_running = await tc.is_method_running()  # Returns True if state is "busy"

# Wait for method completion with polling
await tc.wait_for_method_completion(
    poll_interval=5.0,  # Check every 5 seconds
    timeout=3600.0      # Timeout after 1 hour
)
```

### Temperature Control

See **Recommended Workflows → Set block and lid temperature** for the main usage. Summary: `await tc.set_block_temperature([temp])` or with `lid_temperature=..., wait=False`. ODTC implements this via a PreMethod (no direct SetBlockTemperature command); default lid is 110°C (96-well) or 115°C (384-well).

### Parallel Operations

Per ODTC SiLA spec, certain commands can run in parallel with `ExecuteMethod`:

- ✅ `ReadActualTemperature` - Read temperatures during execution
- ✅ `OpenDoor` / `CloseDoor` - Door operations
- ✅ `StopMethod` - Stop current method
- ❌ `SetParameters` / `GetParameters` - Sequential
- ❌ `GetLastData` - Sequential
- ❌ Another `ExecuteMethod` - Only one method at a time

```python
# Start method
execution = await tc.run_stored_protocol("PCR_30cycles", wait=False)

# These can run in parallel:
temps = await tc.read_temperatures()
door_opening = await tc.open_lid(wait=False)

# Wait for lid to complete
await door_opening

# These will queue/wait:
method2 = await tc.run_stored_protocol("PCR_40cycles", wait=False)  # Waits for method1
```

### CommandExecution vs MethodExecution

- **`CommandExecution`**: Base class for all async commands (door operations, initialize, reset, etc.)
- **`MethodExecution`**: Subclass of `CommandExecution` for method execution with additional features:
  - `is_running()`: Checks if device is in "busy" state
  - `stop()`: Stops the currently running method
  - `method_name`: More semantic than `command_name` for methods

```python
# CommandExecution example
door_opening = await tc.open_lid(wait=False)
await door_opening  # Wait for lid to open

# MethodExecution example (has additional features)
method_exec = await tc.run_stored_protocol("PCR_30cycles", wait=False)
if await method_exec.is_running():
    print(f"Method {method_exec.method_name} is running")
    await method_exec.stop()  # Stop the method
```

## Getting Protocols from Device

### List All Protocol Names (Recommended)

```python
# List all protocol names (ProtocolList: .methods, .premethods, .all, and iterable)
protocol_list = await tc.backend.list_protocols()

for name in protocol_list:
    print(f"Protocol: {name}")
# Or: protocol_list.all for flat list; protocol_list.methods / protocol_list.premethods for split
```

### List Methods and PreMethods Separately

```python
# Returns (method_names, premethod_names); methods are runnable, premethods are setup-only
methods, premethods = await tc.backend.list_methods()
# methods + premethods equals protocol_list.all (from list_protocols())
```

### Get Runnable Protocol by Name

```python
# get_protocol(name) returns Optional[ODTCProtocol] (None for premethods or missing name)
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is not None:
    print(f"Method: {odtc.name}, steps: {len(odtc.steps)}")
    # To edit and re-upload: protocol, config = odtc_method_to_protocol(odtc)
    # To get Protocol view only: protocol, _ = odtc_protocol_to_protocol(odtc)
```

### Get Full MethodSet (Advanced)

```python
# Download all methods and premethods from device
method_set = await tc.backend.get_method_set()  # Returns ODTCMethodSet

# Access methods
for method in method_set.methods:
    print(f"Method: {method.name}, Steps: {len(method.steps)}")

for premethod in method_set.premethods:
    print(f"PreMethod: {premethod.name}")
```

### Inspect Stored Protocol

```python
from pylabrobot.thermocycling.inheco.odtc_model import odtc_protocol_to_protocol

odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is not None:
    protocol, _ = odtc_protocol_to_protocol(odtc)  # Protocol view (stages derived from steps)
    print(odtc)  # Human-readable summary (name, steps, method-level fields)
    await tc.run_protocol(odtc, block_max_volume=50.0)  # backend accepts ODTCProtocol
```

### Display and logging

- **ODTCProtocol** and **ODTCSensorValues**: `print(odtc)` and `print(await tc.backend.read_temperatures())` show labeled summaries. ODTCSensorValues `__str__` is multi-line for display; use `format_compact()` for single-line logs.
- **Wait messages**: When you `await handle`, `handle.wait()`, or `handle.wait_resumable()`, the message logged at INFO is multi-line (command, duration, remaining time) for clear console/notebook display.

## Running Protocols (reference)

- **By name:** See **Recommended Workflows → Run stored protocol by name**. `await tc.run_stored_protocol(name)` or `wait=False` for a handle.
- **Round-trip (modify with thermal performance):** See **Recommended Workflows → Get → modify → upload with config → run**.
- **Block + lid temp:** See **Recommended Workflows → Set block and lid temperature**.
- **In-memory (new protocol):** `await tc.run_protocol(protocol, block_max_volume=50.0)` (upload + execute). New protocols use default overshoot; for best thermal performance, prefer round-trip from an existing device protocol.
- **From XML file:** `method_set = parse_method_set_file("my_methods.xml")` (from `odtc_model`), then `await tc.backend.upload_method_set(method_set)` and `await tc.run_stored_protocol("PCR_30cycles")`.

## ODTCProtocol and Protocol + ODTCConfig Conversion

### Lossless Round-Trip

Conversion between ODTC (device/XML) and PyLabRobot's generic `Protocol` is **lossless** when you keep the `ODTCConfig` returned by `odtc_method_to_protocol(odtc)`. The config preserves method-level and per-step ODTC parameters (overshoot, slopes, PID, etc.).

### How It Works

#### 1. ODTCProtocol → Protocol + ODTCConfig

```python
from pylabrobot.thermocycling.inheco.odtc_model import odtc_method_to_protocol

# get_protocol(name) returns Optional[ODTCProtocol]; then convert for editing
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is None:
    raise ValueError("Protocol not found")
protocol, config = odtc_method_to_protocol(odtc)
```

**What gets preserved in `ODTCConfig`:**

- **Method-level parameters:**
  - `name`, `creator`, `description`, `datetime`
  - `fluid_quantity`, `variant`, `plate_type`
  - `lid_temperature`, `start_lid_temperature`
  - `post_heating`
  - `pid_set` (PID controller parameters)

- **Per-step parameters** (stored in `config.step_settings[step_index]`):
  - `slope` - Temperature ramp rate (°C/s)
  - `overshoot_slope1` - First overshoot ramp rate
  - `overshoot_temperature` - Overshoot target temperature
  - `overshoot_time` - Overshoot hold time
  - `overshoot_slope2` - Second overshoot ramp rate
  - `lid_temp` - Lid temperature for this step
  - `pid_number` - PID controller to use

**What goes into `Protocol`:**

- Temperature targets (from `plateau_temperature`)
- Hold times (from `plateau_time`)
- Stage structure (from loop analysis)
- Repeat counts (from `loop_number`)

#### 2. Protocol + ODTCConfig → ODTCProtocol

```python
from pylabrobot.thermocycling.inheco.odtc_model import protocol_to_odtc_protocol

# Convert back to ODTC (lossless if config preserved)
odtc = protocol_to_odtc_protocol(protocol, config=config)
# Then: await tc.backend.upload_protocol(protocol, name="...", config=config)
```

The conversion uses:
- `Protocol` for temperature/time and stage structure
- `ODTCConfig.step_settings` for per-step overtemp parameters
- `ODTCConfig` for method-level parameters

### Overtemp/Overshoot Parameter Preservation

**Overtemp parameters** (overshoot settings) are ODTC-specific features that allow temperature overshooting for faster heating and improved thermal performance:

- **`overshoot_temperature`**: Target temperature to overshoot to
- **`overshoot_time`**: How long to hold at overshoot temperature
- **`overshoot_slope1`**: Ramp rate to overshoot temperature
- **`overshoot_slope2`**: Ramp rate back to target temperature

These parameters are **not part of the generic Protocol** (which only has target temperature and hold time), so they are preserved in `ODTCConfig.step_settings`.

**Why preservation matters:**

When converting existing ODTC XML protocols to PyLabRobot `Protocol` format, **preserving overshoot parameters is critical for maintaining equivalent thermal performance**. Without these parameters, the converted protocol may have different heating characteristics, potentially affecting PCR efficiency or other temperature-sensitive reactions.

**Current behavior:**
- ✅ **Preserved from XML**: When converting ODTC XML → Protocol+Config, all overshoot parameters are captured in `ODTCConfig.step_settings`
- ✅ **Restored to XML**: When converting Protocol+Config → ODTC XML, overshoot parameters are restored from `ODTCConfig.step_settings`
- ⚠️ **Not generated**: When creating new protocols in PyLabRobot, overshoot parameters default to minimal values (0.0 for temperature/time, 0.1 for slopes)

**Future work:**
- 🔮 **Automatic derivation**: Future enhancements will automatically derive optimal overshoot parameters for PyLabRobot-created protocols based on:
  - Temperature transitions (large jumps benefit more from overshoot)
  - Hardware constraints (variant-specific limits)
  - Thermal characteristics (fluid quantity, plate type)
- 🔮 **Performance optimization**: This will enable PyLabRobot-created protocols to achieve equivalent or improved thermal performance compared to manually-tuned ODTC protocols

**Example of preservation:**

```python
# When converting ODTCProtocol → Protocol + ODTCConfig
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is None:
    raise ValueError("Protocol not found")
protocol, config = odtc_method_to_protocol(odtc)

# Overtemp params stored per step (preserved from original XML)
step_0_overtemp = config.step_settings[0]
print(step_0_overtemp.overshoot_temperature)  # e.g., 100.0 (from original XML)
print(step_0_overtemp.overshoot_time)         # e.g., 5.0 (from original XML)

# When converting back Protocol + ODTCConfig → ODTCProtocol
odtc_restored = protocol_to_odtc_protocol(protocol, config=config)
assert odtc_restored.steps[0].overshoot_temperature == 100.0
```

**Important:** Always preserve the `ODTCConfig` when modifying protocols converted from ODTC XML to maintain equivalent thermal performance. If you create a new protocol without a config, overshoot parameters will use defaults which may result in slower heating.

### Example: Round-Trip Conversion

```python
from pylabrobot.thermocycling.inheco.odtc_model import odtc_method_to_protocol, protocol_to_odtc_protocol

# 1. Get ODTCProtocol from device; convert to Protocol + ODTCConfig for editing
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is None:
    raise ValueError("Protocol not found")
protocol, config = odtc_method_to_protocol(odtc)

# 2. Modify protocol (durations, repeats; keep temperatures when reusing config)
protocol.stages[0].repeats = 35

# 3. Upload (backend calls protocol_to_odtc_protocol internally; config preserves ODTC params)
await tc.backend.upload_protocol(protocol, name="PCR_35cycles", config=config)

# 4. Execute
await tc.run_stored_protocol("PCR_35cycles")
```

### Round-Trip from Device XML

```python
from pylabrobot.thermocycling.inheco.odtc_model import odtc_method_to_protocol

# Full round-trip: Device → ODTCProtocol → Protocol+ODTCConfig → upload → Device

# 1. Get from device
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is None:
    raise ValueError("Protocol not found")
protocol, config = odtc_method_to_protocol(odtc)

# 2. Upload back (preserves all ODTC-specific params via config)
await tc.backend.upload_protocol(protocol, name="PCR_30cycles_restored", config=config)

# 3. Verify round-trip
odtc_restored = await tc.backend.get_protocol("PCR_30cycles_restored")
# Content should match (XML formatting may differ)
```

## DataEvent Collection and Progress

During method execution, the ODTC sends **DataEvent** messages; the backend stores them and derives progress (elapsed time, step/cycle, temperatures). When you **await** an execution handle (`await execution` or `await execution.wait()`), progress is reported every **progress_log_interval** (default 150 s) via log lines or **progress_callback**. Same behavior when using **wait_resumable()** (polling-based wait).

```python
# Start method
execution = await tc.run_stored_protocol("PCR_30cycles", wait=False)

# Progress is logged every progress_log_interval (default 150 s) while you await
await execution  # or await execution.wait()

# Get DataEvents for this execution (raw payloads)
events = await execution.get_data_events()
# Returns: List of DataEvent payload dicts

# Get all collected events (backend-level)
all_events = await tc.backend.get_data_events()
# Returns: {request_id1: [...], request_id2: [...]}
```

**Backend option:** `ODTCBackend(..., progress_log_interval=150.0, progress_callback=...)`. Set `progress_log_interval` to `None` or `0` to disable progress reporting during wait.

## Error Handling

The implementation handles SiLA return codes and state transitions:

- **Return code 1**: Synchronous success (GetStatus, GetDeviceIdentification)
- **Return code 2**: Asynchronous command accepted (ExecuteMethod, OpenDoor, etc.)
- **Return code 3**: Asynchronous command completed successfully (in ResponseEvent)
- **Return code 4**: Device busy (command rejected due to parallelism)
- **Return code 5**: LockId mismatch
- **Return code 6**: Invalid/duplicate requestId
- **Return code 9**: Command not allowed in current state

State transitions are tracked automatically:
- `startup` → `standby` (via Reset)
- `standby` → `idle` (via Initialize)
- `idle` → `busy` (when async command starts)
- `busy` → `idle` (when all commands complete)

## Best Practices

1. **Always call `setup()`** before using the device
2. **Use `wait=False`** for long-running methods to enable parallel operations
3. **Check state** with `is_method_running()` before starting new methods
4. **Preserve `ODTCConfig`** when converting protocols to maintain ODTC-specific parameters (especially overshoot parameters for equivalent thermal performance)
5. **Handle timeouts** when waiting for method completion
6. **Clean up** with `stop()` when done

### Protocol Conversion Best Practices

- **When converting from ODTC XML**: Always preserve the returned `ODTCConfig` alongside the `Protocol` to maintain overshoot parameters and ensure equivalent thermal performance
- **When modifying converted protocols**: Keep the original `ODTCConfig` and only modify the `Protocol` structure (temperatures, times, repeats)
- **When creating new protocols**: Be aware that overshoot parameters will use defaults until automatic derivation is implemented (future work)

## Complete Example

```python
from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling.inheco import ODTCThermocycler
from pylabrobot.thermocycling.inheco.odtc_model import odtc_method_to_protocol
from pylabrobot.thermocycling.standard import Protocol, Stage, Step

tc = ODTCThermocycler(name="odtc", odtc_ip="192.168.1.100", variant=96, child_location=Coordinate(0, 0, 0))
await tc.setup()

# Get ODTCProtocol from device; convert to Protocol + ODTCConfig; modify; upload; run
odtc = await tc.backend.get_protocol("PCR_30cycles")
if odtc is not None:
    protocol, config = odtc_method_to_protocol(odtc)
    protocol.stages[0].repeats = 35
    await tc.backend.upload_protocol(protocol, name="PCR_35cycles", config=config)
    execution = await tc.run_stored_protocol("PCR_35cycles", wait=False)
    await execution

# New protocol (generic Protocol) and run (backend converts via protocol_to_odtc_protocol)
protocol = Protocol(stages=[
    Stage(steps=[
        Step(temperature=[95.0], hold_seconds=30.0),
        Step(temperature=[60.0], hold_seconds=30.0),
        Step(temperature=[72.0], hold_seconds=60.0),
    ], repeats=30)
])
await tc.run_protocol(protocol, block_max_volume=50.0)

await tc.set_block_temperature([37.0])
await tc.stop()
```
