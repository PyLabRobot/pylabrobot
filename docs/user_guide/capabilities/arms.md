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

`direction` is the world yaw of the gripper's front finger, in degrees,
**CCW about +Z with 0° = +X**. You can pass a float, or one of the
cardinal-direction strings:

```python
await lh.iswap.pick_up_resource(plate, direction="left")     # = 180°
await lh.iswap.drop_resource(reader, direction="front")      # = 270°
await lh.iswap.move_to_location(coord, direction=45.0)       # 45° CCW from +X
```

| String      | Degrees | World axis (deck frame) |
|:------------|--------:|:------------------------|
| `"right"`   |   `0°`  | `+X`                    |
| `"back"`    |  `90°`  | `+Y`                    |
| `"left"`    | `180°`  | `-X`                    |
| `"front"`   | `270°`  | `-Y`                    |

## Tips and gotchas

- **Coordinates are in the reference resource's frame** (typically the deck). The arm computes gripper target coordinates from the resource's position, dimensions, and the destination type.
- **`pickup_distance_from_bottom`** controls how far up from the bottom of the resource the gripper grips. If `None`, the resource's `preferred_pickup_location` is used, or a default of 5 mm from the top (`size_z - 5`).
- **Resource tree is updated automatically.** After a successful `drop_resource`, the resource is unassigned from its old parent and assigned to the destination.
- **`GripperOrientation`** is either a {data}`~pylabrobot.capabilities.arms.standard.GripperDirection` string literal (`"front"`, `"right"`, `"back"`, `"left"`) or a float in degrees, measured CCW about world +Z with 0° = +X.
- **`request_gripper_pose()`** queries the hardware for the current end effector position. `get_picked_up_resource()` returns the internally tracked state (no hardware call).

## Supported hardware

```{supported-devices} arm
```

## API reference

See {class}`~pylabrobot.capabilities.arms.arm.GripperArm`, {class}`~pylabrobot.capabilities.arms.orientable_arm.OrientableArm`, {class}`~pylabrobot.capabilities.arms.backend.GripperArmBackend`, and {class}`~pylabrobot.capabilities.arms.backend.OrientableGripperArmBackend`.
