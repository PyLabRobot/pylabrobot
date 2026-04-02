# Arms

Arms are capabilities for picking up, moving, and placing labware (plates, lids, etc.) on the deck. PLR provides two arm types:

- {class}`~pylabrobot.capabilities.arms.arm.GripperArm` -- a fixed-axis gripper arm (e.g. Hamilton core grippers). Grips along a single axis.
- {class}`~pylabrobot.capabilities.arms.orientable_arm.OrientableArm` -- a rotatable gripper arm (e.g. Hamilton iSWAP). Can grip from any direction.

Both inherit from `_BaseArm`, which is a {class}`~pylabrobot.capabilities.capability.Capability`.

## When to use

Use arms to move plates, lids, and other labware between deck positions -- from a hotel to a reader, from a reader to a shaker, from a shaker to a centrifuge, etc.

## Setup

Arms are accessed as an attribute on a liquid handler or standalone arm device:

```python
from pylabrobot.hamilton.star import STAR

lh = STAR(name="star", ...)
await lh.setup()

# the arm is at lh.iswap (OrientableArm) or lh.core_gripper (GripperArm)
await lh.iswap.move_resource(plate, to=heater_shaker)
```

## Walkthrough

### Move a plate (one call)

```python
await lh.iswap.move_resource(plate, to=heater_shaker)
```

### Move a plate (step by step)

```python
await lh.iswap.pick_up_resource(plate)
await lh.iswap.drop_resource(heater_shaker)
```

### Move with intermediate waypoints

```python
await lh.iswap.move_resource(
    plate,
    to=centrifuge_bucket,
    intermediate_locations=[safe_height],  # absolute coordinates
)
```

### OrientableArm: grip direction

```python
from pylabrobot.capabilities.arms.standard import GripDirection

await lh.iswap.pick_up_resource(plate, direction=GripDirection.LEFT)
await lh.iswap.drop_resource(reader, direction=GripDirection.FRONT)
```

## Tips and gotchas

- **Coordinates are in the reference resource's frame** (typically the deck). The arm computes gripper target coordinates from the resource's position, dimensions, and the destination type.
- **`pickup_distance_from_top`** controls how far down from the top face the gripper grips. If `None`, the resource's `preferred_pickup_location` is used, or a default of 5 mm.
- **Resource tree is updated automatically.** After a successful `drop_resource`, the resource is unassigned from its old parent and assigned to the destination.
- **`GripOrientation`** is either a {class}`~pylabrobot.capabilities.arms.standard.GripDirection` enum (`FRONT`, `RIGHT`, `BACK`, `LEFT`) or a float in degrees.
- **`request_gripper_location()`** queries the hardware for the current end effector position. `get_picked_up_resource()` returns the internally tracked state (no hardware call).

## Supported hardware

```{supported-devices} arm
```

## API reference

See {class}`~pylabrobot.capabilities.arms.arm.GripperArm`, {class}`~pylabrobot.capabilities.arms.orientable_arm.OrientableArm`, {class}`~pylabrobot.capabilities.arms.backend.GripperArmBackend`, and {class}`~pylabrobot.capabilities.arms.backend.OrientableGripperArmBackend`.
