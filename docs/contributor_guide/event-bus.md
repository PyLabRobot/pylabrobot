# EventBus Contributor Guide

This guide defines the implementation contract for contributors instrumenting a PLR frontend or
driver. For subscription, event consumption, and the current frontend coverage, see the
[user EventBus guide](../user_guide/machine-agnostic-features/event-bus.md).

The EventBus is intentionally in-process and synchronous. It is an observation mechanism,
not a control mechanism: listener failures must never affect hardware control flow.

## When to emit events

Instrument **semantic public operations**: an operation that a protocol author would recognize
as one action, such as fetching a plate, aspirating, shaking, or moving an arm to a location.
Do not represent every serial, USB, transport, or firmware command as a *semantic* operation.

The current EventBus also supports a separate diagnostic layer. Instrumented transports emit
`io.read` and `io.write`, and the Hamilton USB transport emits `firmware.command.started`,
`.completed`, and `.failed`. Semantic and diagnostic events are complementary: semantic events
describe PLR-level operations, while diagnostic events describe the transport and controller
activity performed to execute them.

An instrumented method remains a no-op with respect to events unless an EventBus with at least
one subscriber is active. Use one of these helpers:

- `@evented_operation(...)` for one public async method.
- `with event_operation(...):` for one logical operation implemented by several calls.
- `emit_event(...)` only for a meaningful state transition that is not an operation lifecycle.

Use a low-level diagnostic event only when the transport boundary itself is useful to observe.
Diagnostic events may inherit the enclosing semantic operation context, but do not define a new
protocol-level action or resource-transfer meaning.

## Event contract

A `PLREvent` always contains:

```python
{
  "sequence": 42,
  "name": "liquid_handler.aspirate.completed",
  "timestamp": "2026-08-10T12:34:56.789012+00:00",
  "context": {...},
  "data": {...},
}
```

Use names in this form:

```text
<component>.<operation>.<lifecycle>
```

Examples from the current implementation:

```text
incubator.fetch_plate.started
liquid_handler.resource_pickup.completed
liquid_handler.tip_pickup.failed
shaker.shake.completed
temperature_controller.wait_for_temperature.completed
precise_flex.move_through_cartesian_poses.completed
```

Every operation scope emits exactly one correlated lifecycle sequence:

```text
.started -> .completed
.started -> .failed
```

The EventBus adds these values to `context` for every lifecycle event:

- `operation`: `<component>.<operation>`
- `operation_id`: a UUID shared by the lifecycle sequence

Callers may add higher-level execution context with `event_context(...)`; for example, a batch
identifier or protocol run identifier. Device code must not invent protocol-specific context.

## Required fields for semantic device operations

Every instrumented hardware operation should provide:

- `device`: the identity of the device or controller issuing the operation. Use
  `resource_reference()` when the frontend is a PLR `Resource`. For a frontend that is not a
  resource, use `device_reference()` with an explicit stable name or provide an equally typed,
  frontend-specific device reference.
- `resources`: direct PLR resources acted on by the operation, represented with
  `resource_reference()`.

`resources` describes the literal object operated on. Do not replace a `Well` with its owning
`Plate`, or a `TipSpot` with its parent `TipRack`, merely for a downstream display. The
reference includes `ancestors` so a consumer can make that presentation choice without changing
the event's meaning.

Use `source` and `destination` only when the operation genuinely transfers a resource between
physical locations. Represent a resource endpoint with `resource_reference()`. Use
`coordinate_reference()` for a geometric endpoint when no PLR resource exists. It preserves the
underlying object's PLR serialization contract, including `Coordinate.type`.

Avoid adding fields solely to simplify one consumer. An event should describe what the PLR API
actually did; dashboards, logs, and integrations can derive their own views from the structured
references.

## Operation templates

### Machine lifecycle

Use for `setup()` and `stop()` on a public PLR machine frontend.

```python
@evented_operation(
  "machine.setup",
  lambda self, **_: {
    "device": device_reference(self, name="plate_reader"),
    "resources": [],
  },
)
async def setup(self, **backend_kwargs):
  ...
```

If the machine frontend itself inherits `Resource`, use `resource_reference(self)` instead.
Do not pass arbitrary controller objects to `resource_reference()` merely because they expose a
`name` attribute.

### Resource transfer

Use for a plate, lid, carrier, or other resource transfer. The direct moved resource is listed in
`resources`; locations are named separately.

```python
with event_operation(
  "incubator.fetch_plate",
  device=resource_reference(self),
  resources=[resource_reference(plate)],
  source=resource_reference(site),
  destination=resource_reference(self.loading_tray),
):
  await self.backend.fetch_plate_to_loading_tray(plate)
```

For pickup and drop, record the resource's invocation state in `.started`. A
`completed_data_factory` may capture its final assignment or pose for `.completed`.
For a pickup, capture `resource.parent` as `source` before the frontend unassigns the moved
resource. Omit `source` if the resource is not currently assigned; do not infer one from a caller
or a physical-deck assumption.

### Liquid handling

Use the direct operated containers in `resources`, plus one `liquid_operations` record per
channel. Each item should include the channel, direct resource reference, owning plate reference
when applicable, and `volume`.

```python
{
  "device": resource_reference(liquid_handler),
  "resources": [resource_reference(well)],
  "liquid_operations": [{
    "channel": 0,
    "resource": resource_reference(well),
    "plate": resource_reference(well.parent),
    "volume": 50.0,
  }],
}
```

### Tip handling

Report each direct `TipSpot`, `TipRack`, or `Trash` resource. For channelized tip actions,
include `tip_operations` with the channel and direct resource.

```python
{
  "device": resource_reference(liquid_handler),
  "resources": [resource_reference(tip_spot)],
  "tip_operations": [{"channel": 0, "resource": resource_reference(tip_spot)}],
}
```

### Thermal and shaking operations

Represent the issuing controller as `device`. A `ResourceHolder` controller should include its
currently loaded direct resource in `resources` when one is assigned at operation start. Do not
infer a resource from broader deck state. Event fields use PLR's
[default units](../user_guide/getting-started/units.md), so their names do not repeat those units:

```python
{
  "device": resource_reference(controller),
  "resources": [resource_reference(controller.resource)],  # only when loaded
  "target_temperature": 37.0,
  "duration": 300.0,
  "speed_rpm": 800.0,
}
```

The `speed_rpm` suffix is explicit because rotational speed differs from PLR's default linear
speed in millimeters per second.

For a protocol-requested temperature dwell, use `temperature_controller.hold_temperature` with
`duration` and the controller's configured `target_temperature` when known. The operation
must not reissue `set_temperature()` or claim that an attached resource reached target
temperature. New direct vendor frontends should emit this same semantic operation independently;
they do not need to inherit from the legacy temperature-controller frontend.

### Centrifuge and loader operations

Use `centrifuge.spin` for one requested centrifuge cycle. Include every directly loaded
resource, including one bucket holder reference per loaded resource when the frontend exposes
individual buckets. Use explicit physical parameters:

```python
{
  "device": device_reference(centrifuge, name=centrifuge.name),
  "resources": [resource_reference(plate)],
  "bucket_resources": [{
    "holder": resource_reference(bucket),
    "resource": resource_reference(plate),
  }],
  "relative_centrifugal_force": 500.0,
  "duration": 60.0,
  "acceleration_fraction": 0.8,
  "deceleration_fraction": 0.8,
}
```

`relative_centrifugal_force` is the dimensionless multiple of standard gravity conventionally
written as x g, which PLR defines as the default unit for relative centrifugal force; it is not a
mass in grams or a force in Newtons.

Use `centrifuge_loader.load` and `centrifuge_loader.unload` for a loader's physical transfer
between its staging holder and a centrifuge bucket. List the direct plate in `resources`, and use
the actual staging holder and bucket as `source` and `destination`.

### Arm/controller motion

Public controller operations should identify the controller in `device` and use PLR's default
units for motion arguments. Add a unit suffix only when a field deliberately differs from the
default, such as a percentage-based speed. Low-level controller operations can be useful to a
diagnostic listener, but higher-level resource-aware wrappers should emit the resource-transfer
events when an arm is actually approaching, picking up, moving, or dropping a PLR resource.

## Failure events

A failed operation retains the original invocation context and adds:

```python
{
  "error_type": type(error).__name__,
  "error_message": str(error),
}
```

Do not swallow or transform the original exception merely to emit an event. The EventBus emits
the `.failed` event and re-raises the same failure. Add structured device-specific error details
only when they are stable and useful independently of the raw message.

## Contributor checklist

When adding EventBus support to a frontend or driver:

1. Choose public semantic operation boundaries; do not decorate transport primitives by default.
2. Use a stable `<component>.<operation>` name and one event scope per logical action.
3. Include `device` and direct `resources` in the context factory.
4. Preserve PLR resource semantics; use ancestry for context rather than substituting resources.
5. Use PLR's default units without repeating them in field names. Add a suffix only when a value
   deliberately uses a different unit or representation, such as `speed_rpm` or `speed_pct`.
6. Include `source` and `destination` only for actual resource transfers.
7. Add tests for `.started`, `.completed`, and `.failed`, including operation-ID correlation.
8. Verify no EventBus listener is required for normal device operation and that listener failures
   cannot alter hardware control flow.

New or existing drivers should adopt these conventions incrementally at their public semantic API
boundaries. Update the user EventBus coverage reference when adding a newly instrumented frontend
or changing an emitted public operation.
