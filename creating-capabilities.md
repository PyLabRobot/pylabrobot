# Creating capabilities

This document describes how to create new capabilities and migrate devices to the
`Device`/`Driver`/`CapabilityBackend`/`Capability` architecture.

## Architecture

```
Device (frontend — user-facing API)
  ├── _driver: Driver (hardware I/O, lifecycle, device-level ops)
  ├── _capabilities: [Capability, ...]
  │     └── Capability (frontend logic for one concern)
  │           └── backend: CapabilityBackend (protocol translation, uses _driver)
```

### Lifecycle

```
Device.setup()
  → driver.setup()                    # open connection, initialize hardware
  → for cap in capabilities:
      cap._on_setup()                 # Capability._on_setup()
        → cap.backend._on_setup()     # CapabilityBackend._on_setup()
        → cap._setup_finished = True

Device.stop()
  → for cap in reversed(capabilities):
      cap._on_stop()                  # Capability._on_stop()
        → cap.backend._on_stop()      # CapabilityBackend._on_stop()
        → cap._setup_finished = False
  → driver.stop()                     # close connection
```

### What goes where

| Layer | Responsibility | Examples |
|-------|---------------|----------|
| **Driver** | I/O (serial, USB, gRPC), connection lifecycle (`setup`/`stop`), device-level operations that don't fit any capability. Exposes **generic** methods like `send(bytes)`, `send_command(str)`, `run_measurement(payload)`. | `send_command()`, `open_door()`, `reset()`, `home()`, `get_configuration()` |
| **CapabilityBackend** | Protocol translation — encodes capability-level operations into driver commands. Holds capability-specific config. Has `_on_setup`/`_on_stop` hooks for initialization after driver connects. | `start_shaking()` → `driver.send_command("shakeOn")`, objective/filter cube config |
| **Capability** | User-facing API, validation, orchestration, convenience methods | `shake(speed, duration)` → calls backend + sleep + stop |
| **Device** | Wires driver + capabilities. Manages lifecycle. | Creates driver and backends in `__init__`, registers `_capabilities` |

### Key classes

| Class | Location | Base |
|-------|----------|------|
| `Driver` | `pylabrobot.device` | `SerializableMixin, ABC` — abstract `setup()`, `stop()` |
| `Device` | `pylabrobot.device` | `SerializableMixin, ABC` — owns `_driver: Driver`, `_capabilities: List[Capability]` |
| `CapabilityBackend` | `pylabrobot.capabilities.capability` | `ABC` — has `_on_setup()`, `_on_stop()` hooks |
| `Capability` | `pylabrobot.capabilities.capability` | `ABC` — owns `backend: CapabilityBackend`, has `_on_setup()`, `_on_stop()` |

### Common mistakes

**Driver mirrors capability interface (WRONG):**
```python
class MyDriver(Driver):
  async def set_temperature(self, temperature: float):  # NO — this is a capability method
    self.io.write(f"SET {temperature}")

class MyTempBackend(TemperatureControllerBackend):
  async def set_temperature(self, temperature: float):
    await self._driver.set_temperature(temperature)  # pointless delegation
```

**Backend encodes protocol (RIGHT):**
```python
class MyDriver(Driver):
  async def send_command(self, cmd: str):  # generic wire method
    await self.io.write(cmd.encode())

class MyTempBackend(TemperatureControllerBackend):
  async def set_temperature(self, temperature: float):
    await self._driver.send_command(f"SET {temperature}")  # protocol lives here
```

The driver is the wire. The backend is the protocol. If a driver method has the same name
as a capability method, something is wrong.

**Initialization in driver.setup() vs backend._on_setup():**
Hardware-specific init that requires the driver to be connected (e.g. "initialize shaker
drive", "configure objectives") belongs in `CapabilityBackend._on_setup()`, not
`Driver.setup()`. The driver's `setup()` should only open the connection.

## Creating a new capability

### 1. Define the capability backend (abstract)

`pylabrobot/capabilities/<name>/backend.py`:

```python
from abc import ABCMeta, abstractmethod
from pylabrobot.capabilities.capability import CapabilityBackend

class ShakerBackend(CapabilityBackend, metaclass=ABCMeta):
  @abstractmethod
  async def start_shaking(self, speed: float): ...

  @abstractmethod
  async def stop_shaking(self): ...

  @property
  @abstractmethod
  def supports_locking(self) -> bool: ...

  @abstractmethod
  async def lock_plate(self): ...

  @abstractmethod
  async def unlock_plate(self): ...
```

One capability, one concern. Only abstract methods for the operations this capability supports.
No `setup()`/`stop()` — use `_on_setup()`/`_on_stop()` (inherited from `CapabilityBackend`)
for initialization that must happen after the driver is connected.

### 2. Define the capability (frontend)

`pylabrobot/capabilities/<name>/<name>.py`:

```python
from pylabrobot.capabilities.capability import Capability
from .backend import ShakerBackend

class ShakingCapability(Capability):
  def __init__(self, backend: ShakerBackend):
    super().__init__(backend=backend)
    self.backend: ShakerBackend = backend  # narrow the type

  async def shake(self, speed: float, duration: float = None):
    """Convenience: shake for a duration then stop."""
    await self.backend.start_shaking(speed=speed)
    if duration:
      await asyncio.sleep(duration)
      await self.backend.stop_shaking()
```

Frontend logic (validation, orchestration, convenience methods) lives here, not in the backend.
The `self.backend: ShakerBackend = backend` line narrows the type from `CapabilityBackend`.

### 3. Export via `__init__.py`

`pylabrobot/capabilities/<name>/__init__.py`:

```python
from .backend import ShakerBackend
from .<name> import ShakingCapability
```

## Implementing a vendor device

### Single-capability device

`pylabrobot/<vendor>/backend.py`:

```python
from pylabrobot.capabilities.fan_control import FanBackend
from pylabrobot.device import Driver

class MyFanDriver(Driver):
  """Owns the hardware connection. Knows how to send bytes on the wire."""

  def __init__(self, port: str):
    self.io = Serial(port=port, baudrate=9600, ...)

  async def setup(self):
    await self.io.setup()

  async def stop(self):
    await self.io.stop()

  async def send(self, command: bytes):
    """Send raw bytes and read response."""
    await self.io.write(command)
    return await self.io.read(64)


class MyFanFanBackend(FanBackend):
  """Translates FanBackend interface into driver commands.

  This is where protocol encoding lives — the backend knows that
  turn_on means sending specific byte sequences via the driver.
  """

  def __init__(self, driver: MyFanDriver):
    self._driver = driver

  async def turn_on(self, intensity: int) -> None:
    await self._driver.send(b"\x01" + bytes([intensity]))

  async def turn_off(self) -> None:
    await self._driver.send(b"\x00")
```

`pylabrobot/<vendor>/<device>.py`:

```python
from pylabrobot.capabilities.fan_control import FanControlCapability
from pylabrobot.device import Device
from .backend import MyFanDriver, MyFanFanBackend

class MyFan(Device):
  def __init__(self, port: str):
    driver = MyFanDriver(port=port)
    super().__init__(driver=driver)
    self._driver: MyFanDriver = driver
    self.fan = FanControlCapability(backend=MyFanFanBackend(driver))
    self._capabilities = [self.fan]
```

### Multi-capability device (shared driver)

When one device supports multiple capabilities, they share a single driver:

```python
class BioShakeDriver(Driver):
  """Serial driver. Owns I/O, device-level ops (reset, home)."""

  def __init__(self, port: str):
    self.io = Serial(port=port, baudrate=9600, ...)

  async def setup(self, skip_home: bool = False):
    await self.io.setup()
    if not skip_home:
      await self.reset()
      await self.home()

  async def stop(self):
    await self.io.stop()

  async def send_command(self, cmd: str) -> Optional[str]:
    """Send an ASCII command, return parsed response."""
    ...

  async def reset(self):
    """Device-level reset — not a capability."""
    ...

  async def home(self):
    """Device-level homing — not a capability."""
    ...


class BioShakeShakerBackend(ShakerBackend):
  """Encodes shaking protocol using the driver."""

  def __init__(self, driver: BioShakeDriver):
    self._driver = driver

  async def start_shaking(self, speed: float):
    await self._driver.send_command(f"setShakeTargetSpeed{int(speed)}")
    await self._driver.send_command("shakeOn")

  async def stop_shaking(self):
    await self._driver.send_command("shakeOff")

  ...


class BioShakeTemperatureBackend(TemperatureControllerBackend):
  """Encodes temperature protocol using the same driver."""

  def __init__(self, driver: BioShakeDriver, supports_active_cooling: bool = False):
    self._driver = driver
    self._supports_active_cooling = supports_active_cooling

  async def set_temperature(self, temperature: float):
    await self._driver.send_command(f"setTempTarget{int(temperature * 10)}")
    await self._driver.send_command("tempOn")

  ...


class BioShake3000T(PlateHolder, Device):
  def __init__(self, name: str, port: str):
    driver = BioShakeDriver(port=port)
    PlateHolder.__init__(self, name=name, ...)
    Device.__init__(self, driver=driver)
    self._driver: BioShakeDriver = driver
    self.tc = TemperatureControlCapability(backend=BioShakeTemperatureBackend(driver))
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.tc, self.shaker]
```

### Backend `_on_setup` / `_on_stop`

If a backend needs to do work after the driver connects (e.g. query hardware configuration),
override `_on_setup()`:

```python
class PicoMicroscopyBackend(MicroscopyBackend):
  def __init__(self, driver: PicoDriver, objectives=None, filter_cubes=None):
    self._driver = driver
    self._objectives = objectives or {}
    self._filter_cubes = filter_cubes or {}

  async def _on_setup(self):
    """Configure objectives and filter cubes after driver connects."""
    for pos, obj in self._objectives.items():
      await self.change_objective(pos, obj)
    for pos, mode in self._filter_cubes.items():
      await self.change_filter_cube(pos, mode)
```

This is called automatically by `Capability._on_setup()` → `backend._on_setup()` during
`Device.setup()`, after `driver.setup()` has completed.

### Device-level operations

Operations that don't fit any capability stay on the driver. Users access them via `_driver`:

```python
class PicoDriver(Driver):
  async def open_door(self): ...
  async def close_door(self): ...
  async def get_configuration(self) -> dict: ...

# Usage:
pico = Pico(name="pico", host="192.168.1.100")
await pico.setup()
await pico._driver.open_door()
```

### Chatterbox backends (testing)

Chatterbox backends are pure `CapabilityBackend` subclasses — they do **not** extend `Driver`.
They have no I/O and return dummy data for device-free testing:

```python
class MyFanChatterboxBackend(FanBackend):
  """No-op backend for testing."""

  async def turn_on(self, intensity: int) -> None:
    pass

  async def turn_off(self) -> None:
    pass
```

To test a capability without a real device, create it directly and call `_on_setup()`:

```python
async def test_something(self):
    backend = MyFanChatterboxBackend()
    cap = FanControlCapability(backend=backend)
    await cap._on_setup()
    await cap.turn_on(intensity=50)
```

## Naming conventions

| Thing | Pattern | Example |
|-------|---------|---------|
| Driver | `<Vendor><Device>Driver` | `BioShakeDriver`, `PicoDriver` |
| Capability backend | `<Vendor><Device><Capability>Backend` | `BioShakeShakerBackend`, `PicoMicroscopyBackend` |
| Chatterbox backend | `<Vendor><Device>ChatterboxBackend` or `<Capability>ChatterboxBackend` | `HamiltonHepaFanChatterboxBackend` |
| Capability (abstract) | `<Name>Capability` | `ShakingCapability`, `FanControlCapability` |
| Capability backend (abstract) | `<Name>Backend` | `ShakerBackend`, `FanBackend` |
| Device | `<Vendor><Device>` or product name | `HamiltonHepaFan`, `BioShake3000T`, `Pico` |

## File layout

For simple devices, driver and backends can live in one file. For complex devices or
when a vendor directory has multiple devices, split into separate files.

**Simple (single file):**
```
pylabrobot/<vendor>/
    __init__.py
    backend.py             # Driver + CapabilityBackend(s) in one file
    <device>.py            # Device frontend
```

**Complex (split):**
```
pylabrobot/<vendor>/
    __init__.py
    driver.py              # Driver only
    <capability>_backend.py  # one file per CapabilityBackend
    <device>.py            # Device frontend
```

**Capability definitions (always this layout):**
```
pylabrobot/capabilities/<name>/
    __init__.py            # exports backend + capability
    backend.py             # abstract CapabilityBackend
    <name>.py              # Capability frontend
```

## Checklist for splitting an existing monolithic backend

When migrating an existing `class FooBackend(SomeCapabilityBackend, Driver)` to the split
architecture, follow these steps:

1. **Read the existing backend class.** Identify: I/O setup, generic send/receive methods,
   device-level ops (door, reset, home), and capability methods.
2. **Create the Driver.** Move I/O, `setup()`/`stop()`, generic send methods, device-level ops,
   and `serialize()`. The driver's `setup()` should only open the connection — move any
   capability-specific init to `_on_setup()` on the backend.
3. **Create the CapabilityBackend(s).** Move capability methods. Each backend gets
   `__init__(self, driver: FooDriver)` and stores `self._driver = driver`. Protocol encoding
   (building command strings, byte payloads) lives here, not on the driver.
4. **Update the Device.** Create driver + backend(s) in `__init__`, wire `_capabilities`.
5. **Update `__init__.py` exports.** Remove old class name, add new driver + backend names.
6. **Update legacy wrappers.** Check `pylabrobot/legacy/` for files that import the old class.
   Update them to create driver + backend(s) internally. Run
   `rg 'OldClassName' pylabrobot/legacy/` to find them.
7. **Preserve docstrings.** Do not remove existing docstrings when moving methods between classes.
8. **Smoke test.** `python -c "from pylabrobot.<vendor> import ..."` to verify imports.

## Making legacy code wrap new code

When a legacy `Machine`/`MachineBackend` module already exists, the goal is to move the
*implementation* into the new architecture while keeping the legacy API unchanged.

### Principles

1. **Legacy types don't change.** The old `MachineBackend` subclass keeps its name and import path.
2. **Implementation moves to new code.** The legacy wrapper creates a driver + backends internally.
3. **`MachineBackend` and `Driver` are independent hierarchies.** Legacy backends never inherit from `Driver`.

### Pattern

```python
# pylabrobot/legacy/<vendor>/<device>_backend.py

from pylabrobot.<vendor>.backend import MyDriver, MyShakerBackend
from pylabrobot.legacy.<category>.backend import LegacyShakerBackend

class LegacyMyDevice(LegacyShakerBackend):
  def __init__(self, port: str):
    self._driver = MyDriver(port=port)
    self._shaker = MyShakerBackend(self._driver)

  async def setup(self):
    await self._driver.setup()
    await self._shaker._on_setup()

  async def stop(self):
    await self._shaker._on_stop()
    await self._driver.stop()

  async def start_shaking(self, speed: float):
    await self._shaker.start_shaking(speed)
```
