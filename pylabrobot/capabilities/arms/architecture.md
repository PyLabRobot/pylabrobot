# Arms architecture

## Coordinate convention

Backends share one rotation convention so swapping a Hamilton iSWAP for a
PreciseFlex (or any future arm) doesn't change what `direction` means at
the call site.

`direction` (and `CartesianPose.rotation.z`) is the world yaw of the
gripper's *front finger*, in degrees, measured **CCW about world +Z
(right-hand rule, looking down)** with **0° = +X**:

| `direction` | World axis | `GripperDirection` (deck frame) |
|------------:|:----------:|:--------------------------------|
|        `0°` |   `+X`     | `"right"`                       |
|       `90°` |   `+Y`     | `"back"`                        |
|      `180°` |   `-X`     | `"left"`                        |
|      `270°` |   `-Y`     | `"front"`                       |

`GripperDirection = Literal["front", "back", "left", "right"]` is a
string-literal alias for these cardinal degrees, used wherever a deck-
relative label reads better than a raw number.

The frontends accept `direction: Union[GripperDirection, float]` and
resolve the label to degrees before handing it to the backend, so
backend implementations only ever see the float — but every backend
must interpret that float under the convention above.

## Frontend hierarchy (capabilities)

```
_BaseArm(Capability)
  │  halt(), park(), get_gripper_location()
  │  resource tracking (pick_up/drop state)
  │
  └── GripperArm
        │  open/close_gripper, is_gripper_closed
        │  pick_up/drop/move at location
        │  pick_up_resource(), drop_resource(), move_resource() (convenience)
        │
        └── OrientableArm
              Arm with rotation. E.g. Hamilton iSWAP, PreciseFlex.
              pick_up/drop/move with direction parameter
```

Frontend mirrors backend hierarchy exactly.
Joint-space methods are backend-only (robot-specific), accessed via `arm.backend`.

## Backend hierarchy (capability backends)

```
_BaseArmBackend(CapabilityBackend)
  │  halt(), park(), get_gripper_location()
  │
  ├── GripperArmBackend
  │     open/close_gripper, is_gripper_closed
  │     pick_up/drop/move at location (no rotation)
  │
  ├── OrientableGripperArmBackend
  │     pick_up/drop/move with direction (float degrees)
  │
  └── ArticulatedGripperArmBackend
        pick_up/drop/move with full Rotation
```

## Mixins (backend)

- `HasJoints` — joint-space control: pick_up/drop/move at joint position, get_joint_position
- `CanFreedrive` — freedrive (manual guidance) mode

## Concrete implementations

| Device | Driver | Arm Backend | Frontend |
|--------|--------|-------------|----------|
| Hamilton STAR (iSWAP) | STARDriver (shared) | `iSWAP(OrientableGripperArmBackend)` | `OrientableArm` |
| Hamilton STAR (core) | STARDriver (shared) | `CoreGripper(GripperArmBackend)` | `Arm` |
| PreciseFlex 400 | `PreciseFlexDriver` | `PreciseFlexArmBackend(OrientableGripperArmBackend, HasJoints, CanFreedrive)` | `OrientableArm` |

## Usage

Arms are capabilities, not devices. They are owned by a Device:

```python
class STAR(Device):
  def __init__(self, ...):
    driver = STARDriver(...)
    super().__init__(driver=driver)
    self.iswap = OrientableArm(backend=iSWAP(driver), reference_resource=deck)
    self.core_gripper = GripperArm(backend=CoreGripper(driver), reference_resource=deck)
    self._capabilities = [self.iswap, self.core_gripper]
```

A standalone arm (like PreciseFlex) is a Device with a single arm capability:

```python
class PreciseFlex400(Device):
  def __init__(
    self, host, port=10100, has_rail=False, timeout=20, gripper_length=162.0, gripper_z_offset=0.0
  ):
    driver = PreciseFlexDriver(host=host, port=port, timeout=timeout)
    super().__init__(driver=driver)
    backend = PreciseFlexArmBackend(
      driver=driver,
      has_rail=has_rail,
      gripper_length=gripper_length,
      gripper_z_offset=gripper_z_offset,
    )
    self.arm = OrientableArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]

# Joint methods accessed via backend (robot-specific):
await pf.arm.backend.move_to_joint_position({1: 0, 2: 90, 3: 45})
await pf.arm.backend.start_freedrive_mode(free_axes=[0])
```
