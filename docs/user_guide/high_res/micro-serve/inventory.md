# Plate inventory

Each entry in `microserve.stackers` is a `MicroServeStacker`, a PLR
`ResourceStack` with `direction="z"`. It retains the stacker's hardware methods
and holds actual `Plate` resources. The fourteen entries remain indexed 0–13.

Inventory is the expected arrangement supplied by your workflow. An empty
`children` list means no plates have been recorded; it does not prove that the
physical stacker is empty. Firmware counts, height measurements, and barcode
scans never manufacture plate definitions or change resource ownership.

## Record the starting arrangement

Use plate definitions that match the physical labware, including lid and stacking
pitch. In this example, `bottom_plate` and `top_plate` are existing `Plate` objects
for the two plates you have inspected in stacker 5:

```python
stacker = microserve.stackers[5]
stacker.assign_child_resource(bottom_plate)
stacker.assign_child_resource(top_plate)

assert stacker.get_top_item() is top_plate
print(stacker.children)  # bottom to top
print(stacker.get_size_z())
```

Assignment records inventory only. It sends no command, moves no hardware, and
does not set the firmware's cached count. Only the top plate can be removed.
Repeating assignment of the same top plate leaves its position unchanged.
Each physical stack must contain one compatible plate geometry; resource
assignment does not validate physical capacity or compatibility.

You can also construct detached stacks with initial inventories. Like PLR's
`ResourceStack`, the constructor takes `resources` **top to bottom**:

```python
from pylabrobot.high_res import HighResMicroServe, MicroServeStacker

stackers = [
    MicroServeStacker(index=i, name=f"carousel_stacker_{i}")
    for i in range(14)
]
# Pass detached stacks in index order. This does not open the connection.
microserve = HighResMicroServe(host="10.253.253.253", stackers=stackers)
```

For example, a detached stack with two plates can be constructed as
`MicroServeStacker(index=5, resources=[top_plate, bottom_plate])`.
Use distinct names when placing several devices' stacks in a resource tree.
`HighResMicroServe(name="carousel_a", ...)` prefixes the default stacker names.

## Geometry and transfer coordinates

For a bare plate with a known `stacking_z_height`, derive the device's height and
stacking pitch while supplying the measured spatula-support thickness:

```python
from pylabrobot.high_res import MicroServePlateDimensions

dimensions = MicroServePlateDimensions.from_plate(
    top_plate, thickness=measured_support_thickness
)
```

The support thickness is the distance from the top to the underside of the wells,
not the plate's overall height. If pitch is unknown, supply `stack_height`
explicitly. Lidded plates always require an explicit measured pitch for that
plate/lid combination; the helper includes the lid in overall height.

Stack resource locations describe storage geometry. They are **not calibrated
robot pickup coordinates**. The MicroServe presents plates at a shared, moving
transfer position. Teach the robot's handoff separately before moving a robot to
it. This driver does not supply a calibrated transfer `PlateHolder`.

## Confirm a pickup

Remember the expected top plate before preparing it:

```python
plate = stacker.get_top_item()
await stacker.prepare_for_unload(dimensions)
```

Have the robot pick that plate and withdraw. Only after successful pickup:

```python
await stacker.confirm_plate_unloaded(plate)
```

If the plate is still assigned to the stack in PLR, confirmation detaches it.
If the robot already reassigned it to its destination, confirmation preserves
that assignment. The lower plates must match the inventory recorded at preparation.
An unexpected inventory change raises an error for inspection.

After the robot clears the mechanism:

```python
await microserve.retract()
```

The angle-reporting preparation uses the same confirmation method. Preparation
and retraction alone never remove a plate from inventory.

## Confirm a placement

Prepare the receiving position with the incoming plate's validated dimensions:

```python
await stacker.prepare_for_load(dimensions)
```

Have the robot place `incoming_plate` and withdraw. After successful placement:

```python
await stacker.confirm_plate_loaded(incoming_plate)
```

Confirmation assigns that plate to the top, or accepts the robot's existing
assignment if it matches the expected inventory. Then retract:

```python
await microserve.retract()
```

Confirmations are assertions of a completed external action, not sensor queries
or robot commands. Call them before retraction while this driver owns the
preparation. Repeating confirmation of the same plate during that handoff does
nothing; confirming a different plate is rejected. Direct resource assignment
remains available for inspected manual inventory corrections.

## Failures and saved inventories

A failed or interrupted preparation leaves inventory unchanged. Reconnecting
does not infer a pickup or placement. After inspecting the device, successful
`reconcile_preparation()` restores preparation ownership and permits explicit
confirmation of the actual external action. Retraction can instead end the
handoff without changing inventory. If the robot's result is uncertain, inspect
and reconcile the physical plates and resource tree before continuing.

You can save the expected inventory without saving a connection or handoff:

```python
import json
from pathlib import Path

path = Path("microserve-inventory.json")
path.write_text(json.dumps([stack.serialize() for stack in microserve.stackers]))
```

Restore and bind it to a controller without opening a connection:

```python
restored = [
    MicroServeStacker.deserialize(data)
    for data in json.loads(path.read_text())
]
microserve = HighResMicroServe(host="10.253.253.253", stackers=restored)
```

Restored stacks are detached until passed to the device constructor. Their
hardware methods fail while detached. Serialization preserves plate resources,
stacker indices, names, metadata, and child locations; it excludes sockets and
all preparation/recovery ownership. Check saved inventory against the actual
machine before use. It is not a checkpoint for resuming an interrupted transfer.

These inventory rules are tested with simulated transfers, including multiple
plates, serialization, and interrupted preparation. Actual robot handoffs and
multi-plate ordering remain unverified on hardware; see [validation](validation.md).
