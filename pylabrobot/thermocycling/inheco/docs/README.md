# ODTC (On-Deck Thermocycler) Implementation Guide

## Overview

The ODTC implementation provides a complete interface for controlling Inheco ODTC thermocyclers via the SiLA (Standard for Laboratory Automation) protocol. It supports:

- **Asynchronous method execution** with blocking and non-blocking modes
- **Round-trip protocol conversion** between ODTC XML format and PyLabRobot's generic `Protocol` format
- **Lossless parameter preservation** including ODTC-specific overtemp/overshoot parameters
- **Parallel command execution** (e.g., reading temperatures during method execution)
- **State tracking and DataEvent collection** for monitoring method progress

## Architecture

### Components

1. **`ODTCSiLAInterface`** (`odtc_sila_interface.py`)
   - Low-level SiLA communication (SOAP over HTTP)
   - Handles parallelism rules, state management, and lockId validation
   - Tracks pending async commands and collects DataEvents
   - Manages state transitions (Startup → Standby → Idle → Busy)

2. **`ODTCBackend`** (`odtc_backend.py`)
   - High-level device control interface
   - Implements `ThermocyclerBackend` interface
   - Provides method execution, status checking, and data retrieval
   - Handles protocol conversion and method upload/download

3. **`InhecoODTC`** (`odtc.py`)
   - Public-facing resource class
   - Supports both 96-well and 384-well formats via `model` parameter
   - Exposes backend methods with PyLabRobot resource interface

4. **`odtc_xml.py`**
   - XML serialization/deserialization for ODTC MethodSet format
   - Conversion between `ODTCMethod` and generic `Protocol`
   - `ODTCConfig` class for preserving ODTC-specific parameters

## Connection and Setup

### Basic Connection

```python
from pylabrobot.thermocycling.inheco import InhecoODTC, ODTCBackend

# Create thermocycler instance (96-well format)
tc = InhecoODTC(
    name="odtc",
    backend=ODTCBackend(odtc_ip="192.168.1.100"),
    model="96"  # Use "384" for 384-well format
)

# Setup establishes HTTP event receiver and initializes device
await tc.setup()
# Device transitions: Startup → Standby → Idle
```

The `setup()` method:
1. Starts HTTP server for receiving SiLA events (ResponseEvent, StatusEvent, DataEvent)
2. Calls `Reset()` to register event receiver URI and move to Standby
3. Calls `Initialize()` to move to Idle (ready for commands)

### Cleanup

```python
await tc.stop()  # Closes HTTP server and connections
```

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

The `CommandExecution` handle provides:

- **`request_id`**: SiLA request ID for tracking DataEvents
- **`command_name`**: Name of the executing command
- **Awaitable interface**: Can be awaited like `asyncio.Task`
- **`wait()`**: Explicit wait for completion
- **`get_data_events()`**: Get DataEvents for this command execution

```python
# Non-blocking door operation
door_opening = await tc.open_door(wait=False)

# Get DataEvents for this execution
events = await door_opening.get_data_events()

# Wait for completion
await door_opening
```

### Asynchronous Method Execution

The ODTC supports both blocking and non-blocking method execution:

#### Blocking Execution (Default)

```python
# Block until method completes
await tc.execute_method("PCR_30cycles", wait=True)
# Returns None when complete
```

#### Non-Blocking Execution with Handle

```python
# Start method and get execution handle
execution = await tc.execute_method("PCR_30cycles", wait=False)
# Returns MethodExecution handle immediately

# Do parallel operations while method runs
temps = await tc.read_temperatures()  # Allowed in parallel!
await tc.open_door()  # Allowed in parallel!

# Wait for completion (multiple options)
await execution  # Await the handle directly
# OR
await execution.wait()  # Explicit wait method
# OR
await tc.wait_for_method_completion()  # Poll-based wait
```

#### MethodExecution Handle

The `MethodExecution` handle extends `CommandExecution` with method-specific features:

- **All `CommandExecution` features**: `request_id`, `command_name`, awaitable interface, `wait()`, `get_data_events()`
- **`method_name`**: Name of executing method (more semantic than `command_name`)
- **`is_running()`**: Check if method is still running (checks device busy state)
- **`stop()`**: Stop the currently running method

```python
execution = await tc.execute_method("PCR_30cycles", wait=False)

# Check status
if await execution.is_running():
    print(f"Method {execution.method_name} still running (ID: {execution.request_id})")

# Get DataEvents for this execution
events = await tc.get_data_events(execution.request_id)

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
execution = await tc.execute_method("PCR_30cycles", wait=False)

# These can run in parallel:
temps = await tc.read_temperatures()
door_opening = await tc.open_door(wait=False)

# Wait for door to complete
await door_opening

# These will queue/wait:
method2 = await tc.execute_method("PCR_40cycles", wait=False)  # Waits for method1
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
method_exec = await tc.execute_method("PCR_30cycles", wait=False)
if await method_exec.is_running():
    print(f"Method {method_exec.method_name} is running")
    await method_exec.stop()  # Stop the method
```

## Getting Protocols from Device

### Get Full MethodSet

```python
# Download all methods and premethods from device
method_set = await tc.get_method_set()  # Returns ODTCMethodSet

# Access methods
for method in method_set.methods:
    print(f"Method: {method.name}, Steps: {len(method.steps)}")

for premethod in method_set.premethods:
    print(f"PreMethod: {premethod.name}")
```

### Get Specific Method by Name

```python
# Get a specific method
method = await tc.get_method_by_name("PCR_30cycles")
if method:
    print(f"Found method: {method.name}")
```

### Convert to Protocol + Config

```python
from pylabrobot.thermocycling.inheco.odtc_xml import odtc_method_to_protocol

# Get method from device
method = await tc.get_method_by_name("PCR_30cycles")

# Convert to Protocol + ODTCConfig (lossless)
protocol, config = odtc_method_to_protocol(method)

# protocol: Generic PyLabRobot Protocol (stages, steps, temperatures, times)
# config: ODTCConfig preserving all ODTC-specific parameters
```

## Running Protocols

### Upload and Execute from XML File

```python
# Upload MethodSet XML file to device
await tc.upload_method_set_from_file("my_methods.xml")

# Execute a method
await tc.execute_method("PCR_30cycles")
```

### Upload and Execute from ODTCMethodSet Object

```python
from pylabrobot.thermocycling.inheco.odtc_xml import parse_method_set_file

# Parse XML file
method_set = parse_method_set_file("my_methods.xml")

# Upload to device
await tc.upload_method_set(method_set)

# Execute
await tc.execute_method("PCR_30cycles")
```

### Convert Protocol to ODTC and Execute

```python
from pylabrobot.thermocycling.inheco.odtc_xml import protocol_to_odtc_method
from pylabrobot.thermocycling.standard import Protocol, Stage, Step

# Create a Protocol
protocol = Protocol(
    stages=[
        Stage(
            steps=[
                Step(temperature=95.0, hold_seconds=30.0),
                Step(temperature=60.0, hold_seconds=30.0),
                Step(temperature=72.0, hold_seconds=60.0),
            ],
            repeats=30
        )
    ]
)

# Convert to ODTCMethod (with default config)
# Note: Overshoot parameters will use defaults (minimal overshoot)
# Future work will automatically derive optimal overshoot parameters
odtc_method = protocol_to_odtc_method(protocol)

# Upload method to device (need to wrap in MethodSet)
from pylabrobot.thermocycling.inheco.odtc_xml import ODTCMethodSet
method_set = ODTCMethodSet(methods=[odtc_method], premethods=[])
await tc.upload_method_set(method_set)

# Execute
await tc.execute_method(odtc_method.name)
```

**Note on performance:** Protocols created directly in PyLabRobot (without an `ODTCConfig` from an existing XML protocol) will use default overshoot parameters, which may result in slower heating compared to manually-tuned ODTC protocols. Future enhancements will automatically derive optimal overshoot parameters for improved thermal performance.

## XML to Protocol + Config Conversion

### Lossless Round-Trip Conversion

The conversion system ensures **lossless round-trip** conversion between ODTC XML format and PyLabRobot's generic `Protocol` format. This is achieved through the `ODTCConfig` companion object that preserves all ODTC-specific parameters.

### How It Works

#### 1. ODTC → Protocol + Config

```python
from pylabrobot.thermocycling.inheco.odtc_xml import odtc_method_to_protocol

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
from pylabrobot.thermocycling.inheco.odtc_xml import protocol_to_odtc_method

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
from pylabrobot.thermocycling.inheco.odtc_xml import (
    odtc_method_to_protocol,
    protocol_to_odtc_method,
    method_set_to_xml,
    parse_method_set
)

# 1. Get method from device (or parse from XML)
method_set = await tc.get_method_set()
method = method_set.methods[0]

# 2. Convert to Protocol + Config
protocol, config = odtc_method_to_protocol(method)

# 3. Modify protocol (generic changes)
protocol.stages[0].repeats = 35  # Change cycle count

# 4. Convert back to ODTC (preserves all ODTC-specific params)
method_modified = protocol_to_odtc_method(protocol, config=config)

# 5. Upload and execute
method_set_modified = ODTCMethodSet(methods=[method_modified], premethods=[])
await tc.upload_method_set(method_set_modified)
await tc.execute_method(method_modified.name)
```

### Round-Trip from Device XML

```python
# Full round-trip: Device XML → Protocol+Config → Device XML

# 1. Get from device
method_set = await tc.get_method_set()
method = method_set.methods[0]

# 2. Convert to Protocol + Config
protocol, config = odtc_method_to_protocol(method)

# 3. Convert back to XML
method_restored = protocol_to_odtc_method(protocol, config=config)
method_set_restored = ODTCMethodSet(methods=[method_restored], premethods=[])
xml_restored = method_set_to_xml(method_set_restored)

# 4. Verify round-trip (should be equivalent)
# (Note: XML formatting may differ, but content should match)
```

## DataEvent Collection

During method execution, the ODTC sends `DataEvent` messages containing experimental data. These are automatically collected:

```python
# Start method
execution = await tc.execute_method("PCR_30cycles", wait=False)

# Get DataEvents for this execution
events = await tc.get_data_events(execution.request_id)
# Returns: {request_id: [event1, event2, ...]}

# Get all collected events
all_events = await tc.get_data_events()
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
- `Startup` → `Standby` (via Reset)
- `Standby` → `Idle` (via Initialize)
- `Idle` → `Busy` (when async command starts)
- `Busy` → `Idle` (when all commands complete)

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
from pylabrobot.thermocycling.inheco import InhecoODTC, ODTCBackend
from pylabrobot.thermocycling.inheco.odtc_xml import odtc_method_to_protocol

# Setup
tc = InhecoODTC(
    name="odtc",
    backend=ODTCBackend(odtc_ip="192.168.1.100"),
    model="96"  # Use "384" for 384-well format
)
await tc.setup()

# Get method from device
method = await tc.get_method_by_name("PCR_30cycles")
if not method:
    raise ValueError("Method not found")

# Convert to Protocol + Config
protocol, config = odtc_method_to_protocol(method)

# Modify protocol
protocol.stages[0].repeats = 35

# Convert back (preserves all ODTC params including overtemp)
from pylabrobot.thermocycling.inheco.odtc_xml import protocol_to_odtc_method, ODTCMethodSet
method_modified = protocol_to_odtc_method(protocol, config=config)

# Upload and execute
method_set = ODTCMethodSet(methods=[method_modified], premethods=[])
await tc.upload_method_set(method_set)

# Execute with parallel operations
execution = await tc.execute_method(method_modified.name, wait=False)
temps = await tc.read_temperatures()  # Parallel operation
await execution  # Wait for completion

# Cleanup
await tc.stop()
```
