# Arms

Arms are capabilities for picking up, moving, and placing labware (plates, lids, etc.) on the deck. PLR provides two arm types:

- {class}`~pylabrobot.capabilities.arms.arm.FixedAxisGripperArm`: a fixed-axis gripper arm (e.g. Hamilton core grippers). Grips along a single deck-fixed axis.
- {class}`~pylabrobot.capabilities.arms.orientable_arm.OrientableGripperArm`: a rotatable gripper arm (e.g. Hamilton iSWAP). Can grip from any direction.
- {class}`~pylabrobot.capabilities.arms.articulated_arm.ArticulatedGripperArm`: a fully articulated arm (e.g. UFACTORY xArm 6). Pick/drop with arbitrary 3D rotation.

All three inherit from {class}`~pylabrobot.capabilities.arms.arm.GripperArm` (the abstract base that owns gripper-width control), which in turn extends `_BaseArm`, a {class}`~pylabrobot.capabilities.capability.Capability`.

## When to use

Use arms to move plates, lids, and other labware between deck positions: from a hotel to a reader, from a reader to a shaker, from a shaker to a centrifuge, etc.

## Setup

Arms are accessed as an attribute on a liquid handler or standalone arm device:

```python
from pylabrobot.hamilton.star import STAR

lh = STAR(name="star", ...)
await lh.setup()

# the arm is at lh.iswap (OrientableGripperArm) or lh.core_gripper (FixedAxisGripperArm)
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

### OrientableGripperArm: grip direction

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

## Grippers

Gripper actuation goes through a single fundamental call, `move_gripper(width, force_sensing)`, which has two modes:

- `force_sensing=False`: drive the jaws to `width` mm without force feedback. The jaws reach exactly that width.
- `force_sensing=True`: close toward `width` mm with force feedback, stopping on contact. The final width may be larger than the target.

Widths are always in mm. Each backend declares its hardware limits via `min_gripper_width` and `max_gripper_width` (either may be `None` if the gripper has no commandable open/close at that end).

`open_gripper()` and `close_gripper()` are convenience wrappers built on top: `open_gripper()` calls `move_gripper(max_gripper_width, force_sensing=False)` and `close_gripper()` calls `move_gripper(min_gripper_width, force_sensing=True)`. They take no width but still forward `backend_params` so backend-specific options (e.g. `iSWAPBackend.GripParams`) work the same as with `move_gripper`. They raise `NotImplementedError` if the corresponding limit is `None`.

## Supported hardware

```{supported-devices} arm
```

## API reference

See {class}`~pylabrobot.capabilities.arms.arm.GripperArm`, {class}`~pylabrobot.capabilities.arms.arm.FixedAxisGripperArm`, {class}`~pylabrobot.capabilities.arms.orientable_arm.OrientableGripperArm`, {class}`~pylabrobot.capabilities.arms.articulated_arm.ArticulatedGripperArm`, {class}`~pylabrobot.capabilities.arms.backend.GripperArmBackend`, and {class}`~pylabrobot.capabilities.arms.backend.OrientableGripperArmBackend`.
