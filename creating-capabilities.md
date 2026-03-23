# Creating capabilities

This document describes how to create new capabilities and how to migrate legacy
`Machine`/`MachineBackend` modules to the new `Device`/`Capability`/`DeviceBackend` architecture.

## Architecture overview

**Old (legacy):** A `Machine` owns a single `MachineBackend`. The frontend class contains all
the logic and calls backend methods directly.

```
Machine (frontend)
  └── MachineBackend (abstract, one big interface)
        └── ConcreteBackend (vendor implementation)
```

**New:** A `Device` owns a `DeviceBackend` and one or more `Capability` objects. Each capability
is a focused interface (e.g. shaking, temperature control) with its own backend type. Frontend
logic lives in the capability, not the device.

```
Device
  ├── ShakerBackend (DeviceBackend subclass)
  ├── ShakingCapability (owns a reference to the backend)
  └── TemperatureControlCapability (owns a reference to the same backend)
```

### Key classes

| Class | Location | Role |
|-------|----------|------|
| `DeviceBackend` | `pylabrobot.device` | Base for all new backends. Abstract `setup()` and `stop()`. |
| `Device` | `pylabrobot.device` | Base for all new devices. Manages capabilities lifecycle. |
| `Capability` | `pylabrobot.capabilities.capability` | Base for capabilities. Owned by a `Device`. |
| `MachineBackend` | `pylabrobot.legacy.machines.backend` | Legacy backend base. Independent from `DeviceBackend`. |
| `Machine` | `pylabrobot.legacy.machines.machine` | Legacy frontend base. |

## Creating a new capability

### 1. Define the backend

Create an abstract backend in `pylabrobot/capabilities/<name>/backend.py`:

```python
from abc import ABCMeta, abstractmethod
from pylabrobot.device import DeviceBackend

class ShakerBackend(DeviceBackend, metaclass=ABCMeta):
  @abstractmethod
  async def start_shaking(self, speed: float): ...

  @abstractmethod
  async def stop_shaking(self): ...
```

The backend defines *what* operations are possible. Keep it minimal — one capability, one concern.

### 2. Define the capability

Create the capability in `pylabrobot/capabilities/<name>/<name>.py`:

```python
from pylabrobot.capabilities.capability import Capability
from .backend import ShakerBackend

class ShakingCapability(Capability):
  def __init__(self, backend: ShakerBackend):
    super().__init__(backend=backend)
    self.backend: ShakerBackend = backend

  async def shake(self, speed: float, duration: float = None):
    await self.backend.start_shaking(speed=speed)
    if duration:
      await asyncio.sleep(duration)
      await self.backend.stop_shaking()
```

Frontend logic (validation, orchestration, convenience methods) lives here, not in the backend.

### 3. Implement vendor backends

In `pylabrobot/<vendor>/`, create a concrete backend and device:

```python
from pylabrobot.capabilities.shaking import ShakerBackend, ShakingCapability
from pylabrobot.device import Device

class MyVendorShakerBackend(ShakerBackend):
  async def setup(self): ...
  async def stop(self): ...
  async def start_shaking(self, speed: float): ...
  async def stop_shaking(self): ...

class MyVendorShaker(Device):
  def __init__(self, backend: MyVendorShakerBackend):
    super().__init__(backend=backend)
    self.shaking = ShakingCapability(backend=backend)
    self._capabilities = [self.shaking]
```

## Making legacy code wrap new code

When a legacy module already exists, the goal is to move the *implementation* into capabilities
while keeping the legacy frontend and backend interfaces unchanged. Users of the old API should
not need to change anything.

### Principles

1. **Legacy types don't change.** The old `MachineBackend` subclass keeps its name, its methods,
   and its import path. Existing user code that subclasses it must keep working.

2. **Implementation moves to capabilities.** The legacy frontend delegates to capability objects
   internally. This avoids duplicating logic in both old and new code paths.

3. **`MachineBackend` and `DeviceBackend` are independent hierarchies.** They are structurally
   similar but intentionally separate. Legacy backends never inherit from `DeviceBackend`.

4. **Always use adapters.** Even when the old and new backend signatures happen to match today,
   use an adapter. This protects against silent breakage if the new capability backend changes
   later. The adapter is the single point where old meets new.

### Adapter pattern

Every legacy frontend that delegates to a capability needs an adapter. The adapter:
- Implements the new capability backend interface (`DeviceBackend` subclass)
- Wraps a legacy backend instance and delegates to it
- Translates between old and new signatures if they differ
- Has no-op `setup()`/`stop()` since lifecycle is managed by the legacy `Machine`

```python
# In the legacy frontend module (e.g. pylabrobot/legacy/shaking/shaker.py)

from pylabrobot.capabilities.shaking import ShakerBackend as _NewShakerBackend, ShakingCapability

class _ShakingAdapter(_NewShakerBackend):
  """Adapts a legacy ShakerBackend to the new ShakerBackend interface."""
  def __init__(self, legacy: ShakerBackend):
    self._legacy = legacy
  async def setup(self): pass
  async def stop(self): pass
  async def start_shaking(self, speed: float):
    await self._legacy.start_shaking(speed)
  async def stop_shaking(self):
    await self._legacy.stop_shaking()
  @property
  def supports_locking(self) -> bool:
    return self._legacy.supports_locking
  async def lock_plate(self):
    await self._legacy.lock_plate()
  async def unlock_plate(self):
    await self._legacy.unlock_plate()

class Shaker(Machine):
  def __init__(self, backend: ShakerBackend):  # legacy ShakerBackend
    super().__init__(backend=backend)
    self._cap = ShakingCapability(backend=_ShakingAdapter(backend))

  async def shake(self, speed, duration=None):
    await self._cap.shake(speed=speed, duration=duration)
```

### One-to-many split (e.g. PlateReader)

When the old backend is a "god object" that gets split into multiple capabilities:

```
Old: PlateReaderBackend(MachineBackend)
       read_absorbance(), read_fluorescence(), read_luminescence(), open(), close()

New: AbsorbanceBackend(DeviceBackend)    with read_absorbance()
     FluorescenceBackend(DeviceBackend)  with read_fluorescence()
     LuminescenceBackend(DeviceBackend)  with read_luminescence()
```

The old `PlateReaderBackend` has `read_absorbance()` but is not an `AbsorbanceBackend`. You can't
pass it directly to `AbsorbanceCapability`. Use **adapters** in the legacy frontend:

```python
# pylabrobot/legacy/plate_reading/plate_reader.py

class _AbsorbanceAdapter(AbsorbanceBackend):
  """Adapts a legacy PlateReaderBackend to the AbsorbanceBackend interface."""
  def __init__(self, legacy: PlateReaderBackend):
    self._legacy = legacy

  async def setup(self): pass   # lifecycle managed by the legacy Machine
  async def stop(self): pass

  async def read_absorbance(self, plate, wells, wavelength):
    # translate between old and new signatures if needed
    return await self._legacy.read_absorbance(plate, wells, wavelength)


class PlateReader(Machine):
  def __init__(self, backend: PlateReaderBackend):
    super().__init__(backend=backend)
    self._absorbance = AbsorbanceCapability(backend=_AbsorbanceAdapter(backend))
    self._fluorescence = FluorescenceCapability(backend=_FluorescenceAdapter(backend))
    self._luminescence = LuminescenceCapability(backend=_LuminescenceAdapter(backend))
```

Adapters belong in the legacy layer. They are the only place that knows about both the old and
new interfaces. If the new backend signature changes later, you update the adapter — the old
`PlateReaderBackend` interface is unaffected.

### Case 3: Signature mismatch

When the old and new backends have the same method name but different signatures:

```
Old: read_absorbance(plate, wells, wavelength) -> List[Dict]
New: read_absorbance(plate, wells, wavelength) -> List[AbsorbanceResult]
```

This is handled the same way as Case 2 — the adapter translates:

```python
class _AbsorbanceAdapter(AbsorbanceBackend):
  async def read_absorbance(self, plate, wells, wavelength) -> List[AbsorbanceResult]:
    dicts = await self._legacy.read_absorbance(plate, wells, wavelength)
    return [AbsorbanceResult(data=d["data"], wavelength=wavelength, ...) for d in dicts]
```

### Summary

| Situation | Fix |
|-----------|-----|
| 1:1 mapping, same signatures | Adapter in legacy frontend (protects against future divergence) |
| 1:N split | Adapter per capability in the legacy frontend |
| Signature mismatch | Adapter that translates between old and new signatures |

In all cases, the adapter lives in the legacy layer and is the only code that knows about both
the old and new interfaces.
