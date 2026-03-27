# Arms architecture

## Frontend hierarchy (capabilities)

```
_BaseArm(Capability)
  │  halt(), park(), get_gripper_location()
  │  resource tracking (pick_up/drop state)
  │
  ├── Arm
  │     Simple arm, no rotation. E.g. Hamilton core grippers.
  │     open/close_gripper, pick_up/drop/move at location
  │     pick_up_resource(), drop_resource(), move_resource() (convenience)
  │
  └── OrientableArm
        Arm with rotation. E.g. Hamilton iSWAP, PreciseFlex.
        pick_up/drop/move with direction parameter
```

Gripper vs suction is a backend distinction, not a frontend one.
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

## Usage

Arms are capabilities, not devices. They are owned by a Device:

```python
class STAR(Device):
  def __init__(self, ...):
    driver = STARDriver(...)
    super().__init__(driver=driver)
    self.iswap = OrientableArm(backend=iSWAPBackend(driver), reference_resource=deck)
    self.core_gripper = Arm(backend=CoreGripperBackend(driver), reference_resource=deck)
    self._capabilities = [self.iswap, self.core_gripper]
```

A standalone arm (like PreciseFlex) is a Device with a single arm capability:

```python
class PreciseFlex400(Device):
  def __init__(self, ...):
    driver = PreciseFlexDriver(...)
    super().__init__(driver=driver)
    self.arm = OrientableArm(backend=PF400Backend(driver), reference_resource=deck)
    self._capabilities = [self.arm]

# Joint methods accessed via backend:
await pf.arm.backend.move_to_joint_position({0: 0, 1: 90, 2: 45})
```
