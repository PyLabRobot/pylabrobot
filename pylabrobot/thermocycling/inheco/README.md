# ODTC (On-Deck Thermocycler) Implementation Guide

## Overview

Interface for Inheco ODTC thermocyclers via SiLA (SOAP over HTTP). Supports asynchronous method execution (blocking and non-blocking), round-trip protocol conversion (ODTC XML ↔ PyLabRobot `Protocol` with lossless ODTC parameters), parallel commands (e.g. read temperatures during run), and DataEvent collection.

**New users:** Start with **Connection and Setup**, then **Recommended Workflows** (run by name, round-trip for thermal performance, set block/lid temp). Use **Running Commands** and **Getting Protocols** for async handles and device introspection; **XML to Protocol + Config** for conversion detail.

## Architecture

- **`ODTCSiLAInterface`** (`odtc_sila_interface.py`) — SiLA SOAP layer: `send_command` / `start_command`, parallelism rules, state machine (Startup → Standby → Idle → Busy), lockId, DataEvents.
- **`ODTCBackend`** (`odtc_backend.py`) — Implements `ThermocyclerBackend`: method execution, protocol conversion, upload/download, status.
- **`ODTCThermocycler`** (`odtc_thermocycler.py`) — Preferred resource: takes `odtc_ip`, `variant` (96/384 or 960000/384000), uses ODTC dimensions (147×298×130 mm). Alternative: generic `Thermocycler` with `ODTCBackend` for custom sizing.
- **`odtc_model.py`** — MethodSet XML (de)serialization, `ODTCMethod` ↔ `Protocol` conversion, `ODTCConfig` for ODTC-specific parameters.

## Protocol vs Method: Naming Conventions

Understanding the distinction between **Protocol** and **Method** is crucial for using the ODTC API correctly:

### Protocol (PyLabRobot)
- **`Protocol`**: PyLabRobot's generic protocol object (from `pylabrobot.thermocycling.standard`)
  - Contains `Stage` objects with `Step` objects
  - Defines temperatures, hold times, and cycle repeats
  - Hardware-agnostic (works with any thermocycler)
  - Example: `Protocol(stages=[Stage(steps=[Step(temperature=95.0, hold_seconds=30.0)])])`

### Method (ODTC Device)
- **`ODTCMethod`** or **`ODTCPreMethod`**: ODTC-specific XML-defined method stored on the device. In ODTC/SiLA, a **method** is the device's runnable protocol (thermocycling program).
  - Contains ODTC-specific parameters (overshoot, slopes, PID settings)
  - Stored on the device with a unique **method name** (string identifier; SiLA: `methodName`)
  - Example: `"PCR_30cycles"` is a method name stored on the device

### Method Name (String)
- **Method name**: A string identifier for a method stored on the device
  - Examples: `"PCR_30cycles"`, `"my_pcr"`, `"plr_currentProtocol"`
  - Used to reference methods when executing: `await tc.run_protocol(method_name="PCR_30cycles")`
  - Can be a Method or PreMethod name (both are stored on the device)

### Key API

- **Resource:** `tc.run_protocol(protocol, block_max_volume)` — in-memory (upload + execute); `tc.run_stored_protocol(name)` — by name (ODTC only).
- **Backend:** `tc.backend.list_protocols()`, `get_protocol(name)` (runnable methods only; premethods → `None`), `upload_protocol(...)`, `set_block_temperature(...)` (PreMethod), `get_default_config()`, `get_constraints()`, `execute_method(method_name)` (low-level).

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

### Cleanup

```python
await tc.stop()  # Closes HTTP server and connections
```

## Recommended Workflows

Use these patterns for the best balance of simplicity and thermal performance.

### 1. Run stored protocol by name

**Use when:** The protocol (method) is already on the device. Single instrument call; no upload.

```python
# List names: methods and premethods
names = await tc.backend.list_protocols()  # e.g. ["PCR_30cycles", "my_pcr", ...]

# Run by name (blocking or non-blocking)
await tc.run_stored_protocol("PCR_30cycles")
# Or with handle: execution = await tc.run_stored_protocol("PCR_30cycles", wait=False); await execution
```

### 2. Get → modify → upload with config → run (round-trip for thermal performance)

**Use when:** You want to change an existing device protocol (e.g. cycle count) while keeping equivalent thermal performance. Preserving `ODTCConfig` keeps overshoot and other ODTC parameters from the original.

```python
# Get runnable protocol from device (returns None for premethods)
stored = await tc.backend.get_protocol("PCR_30cycles")
if not stored:
    raise ValueError("Protocol not found")
protocol, config = stored.protocol, stored.config

# Modify only durations or cycle counts; keep temperatures unchanged
# ODTCConfig is tuned for the original temperature setpoints—change temps and tuning may be wrong
protocol.stages[0].repeats = 35  # Safe: cycle count
# protocol.stages[0].steps[0].duration = 120  # Safe: hold duration
# Do NOT change plateau_temperature / setpoints when reusing config

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
await tc.open_door()  # Returns None when complete
await tc.close_door()
await tc.initialize()
await tc.reset()
```

#### Non-Blocking Execution with Handle

```python
# Start command and get execution handle
door_opening = await tc.open_door(wait=False)
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
door_opening = await tc.open_door(wait=False)

# Get DataEvents for this execution
events = await door_opening.get_data_events()

# Wait for completion
await door_opening
```

### Method Execution

- **Blocking:** `await tc.run_stored_protocol("PCR_30cycles")` or `await tc.run_protocol(protocol, block_max_volume=50.0)` (upload + execute).
- **Non-blocking:** `execution = await tc.run_stored_protocol("PCR_30cycles", wait=False)`; then `await execution` or `await execution.wait()` or `await tc.wait_for_method_completion()`.
- While a method runs you can call `read_temperatures()`, `open_door(wait=False)`, etc. (parallel where allowed).

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
door_opening = await tc.open_door(wait=False)

# Wait for door to complete
await door_opening

# These will queue/wait:
method2 = await tc.run_protocol(method_name="PCR_40cycles", wait=False)  # Waits for method1
```

### CommandExecution vs MethodExecution

- **`CommandExecution`**: Base class for all async commands (door operations, initialize, reset, etc.)
- **`MethodExecution`**: Subclass of `CommandExecution` for method execution with additional features:
  - `is_running()`: Checks if device is in "busy" state
  - `stop()`: Stops the currently running method
  - `method_name`: More semantic than `command_name` for methods

```python
# CommandExecution example
door_opening = await tc.open_door(wait=False)
await door_opening  # Wait for door to open

# MethodExecution example (has additional features)
method_exec = await tc.run_stored_protocol("PCR_30cycles", wait=False)
if await method_exec.is_running():
    print(f"Method {method_exec.method_name} is running")
    await method_exec.stop()  # Stop the method
```

## Getting Protocols from Device

### List All Protocol Names (Recommended)

```python
# List all protocol names (both Methods and PreMethods)
protocol_names = await tc.backend.list_protocols()
# Returns: ["PCR_30cycles", "my_pcr", "PRE25", "plr_currentProtocol", ...]

for name in protocol_names:
    print(f"Protocol: {name}")
```

### Get Runnable Protocol by Name

```python
# Get a runnable protocol by name (returns StoredProtocol or None for premethods)
stored = await tc.backend.get_protocol("PCR_30cycles")
if stored:
    print(f"Protocol: {stored.name}")
    # stored.protocol: Protocol; stored.config: ODTCConfig
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
# Get runnable protocol from device (StoredProtocol has protocol + config)
stored = await tc.backend.get_protocol("PCR_30cycles")
if stored:
    # stored.protocol: Generic PyLabRobot Protocol (stages, steps, temperatures, times)
    # stored.config: ODTCConfig preserving all ODTC-specific parameters
    await tc.run_protocol(stored.protocol, block_max_volume=50.0, config=stored.config)
```

## Running Protocols (reference)

- **By name:** See **Recommended Workflows → Run stored protocol by name**. `await tc.run_stored_protocol(name)` or `wait=False` for a handle.
- **Round-trip (modify with thermal performance):** See **Recommended Workflows → Get → modify → upload with config → run**.
- **Block + lid temp:** See **Recommended Workflows → Set block and lid temperature**.
- **In-memory (new protocol):** `await tc.run_protocol(protocol, block_max_volume=50.0)` (upload + execute). New protocols use default overshoot; for best thermal performance, prefer round-trip from an existing device protocol.
- **From XML file:** `method_set = parse_method_set_file("my_methods.xml")` (from `odtc_model`), then `await tc.backend.upload_method_set(method_set)` and `await tc.run_stored_protocol("PCR_30cycles")`.

## XML to Protocol + Config Conversion

### Lossless Round-Trip Conversion

The conversion system ensures **lossless round-trip** conversion between ODTC XML format and PyLabRobot's generic `Protocol` format. This is achieved through the `ODTCConfig` companion object that preserves all ODTC-specific parameters.

### How It Works

#### 1. ODTC → Protocol + Config

```python
from pylabrobot.thermocycling.inheco.odtc_model import odtc_method_to_protocol

# Convert ODTC method to Protocol + Config
protocol, config = odtc_method_to_protocol(odtc_method)
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

#### 2. Protocol + Config → ODTC

```python
from pylabrobot.thermocycling.inheco.odtc_model import protocol_to_odtc_method

# Convert back to ODTC method (lossless if config preserved)
odtc_method = protocol_to_odtc_method(protocol, config=config)
```

The conversion uses:
- `Protocol` for temperature/time structure
- `ODTCConfig.step_settings` for per-step overtemp parameters
- `ODTCConfig` defaults for method-level parameters

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
# When converting ODTC → Protocol + Config
protocol, config = odtc_method_to_protocol(odtc_method)

# Overtemp params stored per step (preserved from original XML)
step_0_overtemp = config.step_settings[0]
print(step_0_overtemp.overshoot_temperature)  # e.g., 100.0 (from original XML)
print(step_0_overtemp.overshoot_time)         # e.g., 5.0 (from original XML)

# When converting back Protocol + Config → ODTC
odtc_method_restored = protocol_to_odtc_method(protocol, config=config)

# Overtemp params restored from config.step_settings
# This ensures equivalent thermal performance to original
assert odtc_method_restored.steps[0].overshoot_temperature == 100.0
```

**Important:** Always preserve the `ODTCConfig` when modifying protocols converted from ODTC XML to maintain equivalent thermal performance. If you create a new protocol without a config, overshoot parameters will use defaults which may result in slower heating.

### Example: Round-Trip Conversion

```python
from pylabrobot.thermocycling.inheco.odtc_model import (
    odtc_method_to_protocol,
    protocol_to_odtc_method,
    method_set_to_xml,
    parse_method_set
)

# 1. Get runnable protocol from device
stored = await tc.backend.get_protocol("PCR_30cycles")
if not stored:
    raise ValueError("Protocol not found")
protocol, config = stored.protocol, stored.config

# 2. Modify protocol (generic changes)
protocol.stages[0].repeats = 35  # Change cycle count

# 3. Upload modified protocol (preserves all ODTC-specific params via config)
await tc.backend.upload_protocol(protocol, name="PCR_35cycles", config=config)

# 4. Execute
await tc.run_stored_protocol("PCR_35cycles")
```

### Round-Trip from Device XML

```python
# Full round-trip: Device Method → Protocol+Config → Device Method

# 1. Get from device
stored = await tc.backend.get_protocol("PCR_30cycles")
if not stored:
    raise ValueError("Protocol not found")
protocol, config = stored.protocol, stored.config

# 2. Upload back to device (preserves all ODTC-specific params via config)
await tc.backend.upload_protocol(protocol, name="PCR_30cycles_restored", config=config)

# 3. Verify round-trip by comparing protocols
stored_restored = await tc.backend.get_protocol("PCR_30cycles_restored")
# Protocols should be equivalent (XML formatting may differ, but content should match)
```

## DataEvent Collection

During method execution, the ODTC sends `DataEvent` messages containing experimental data. These are automatically collected:

```python
# Start method
execution = await tc.run_stored_protocol("PCR_30cycles", wait=False)

# Get DataEvents for this execution
events = await execution.get_data_events()
# Returns: List of DataEvent objects

# Get all collected events (backend-level)
all_events = await tc.backend.get_data_events()
# Returns: {request_id1: [...], request_id2: [...]}
```

**Note:** DataEvent parsing and progress tracking are planned for future implementation. Currently, raw event payloads are stored for later analysis.

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
from pylabrobot.thermocycling.standard import Protocol, Stage, Step

tc = ODTCThermocycler(name="odtc", odtc_ip="192.168.1.100", variant=96, child_location=Coordinate(0, 0, 0))
await tc.setup()

# Get protocol from device, modify, run
stored = await tc.backend.get_protocol("PCR_30cycles")
if stored:
    protocol, config = stored.protocol, stored.config
    protocol.stages[0].repeats = 35
    await tc.backend.upload_protocol(protocol, name="PCR_35cycles", config=config)
    execution = await tc.run_stored_protocol("PCR_35cycles", wait=False)
    await execution

# New protocol and run
protocol = Protocol(stages=[Stage(steps=[Step(95.0, 30.0), Step(60.0, 30.0), Step(72.0, 60.0)], repeats=30)])
await tc.run_protocol(protocol, block_max_volume=50.0)

await tc.set_block_temperature([37.0])
await tc.stop()
```
