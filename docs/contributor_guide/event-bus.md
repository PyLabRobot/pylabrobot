# EventBus Operation Semantics

The PLR EventBus provides optional, structured execution events for applications that need
observability without parsing device logs. This guide defines the contract for contributors
instrumenting a frontend or driver.

The EventBus is intentionally in-process and synchronous. It is an observation mechanism,
not a control mechanism: listener failures must never affect hardware control flow.

## When to emit events

Instrument **semantic public operations**: an operation that a protocol author would recognize
as one action, such as fetching a plate, aspirating, shaking, or moving an arm to a location.
Do not represent every serial, USB, transport, or firmware command as a *semantic* operation.

The current EventBus also supports a separate diagnostic layer. Instrumented transports emit
`io.read` and `io.write`, and the Hamilton USB transport emits `firmware.command.started`,
`.completed`, and `.failed`. These records are useful for correlation and diagnostics, but are
not a replacement for semantic frontend events. Consumers should normally filter them from
operator timelines and high-level notifications.

An instrumented method remains a no-op with respect to events unless an EventBus with at least
one subscriber is active. Use one of these helpers:

- `@evented_operation(...)` for one public async method.
- `with event_operation(...):` for one logical operation implemented by several calls.
- `emit_event(...)` only for a meaningful state transition that is not an operation lifecycle.

Use a low-level diagnostic event only when the transport boundary itself is useful to observe.
Diagnostic events may inherit the enclosing semantic operation context, but do not define a new
protocol-level action or resource-transfer meaning.

## Universal event contract

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

- `device`: `resource_reference()` for the device or controller issuing the operation.
- `resources`: direct PLR resources acted on by the operation, represented with
  `resource_reference()`.

`resources` describes the literal object operated on. Do not replace a `Well` with its owning
`Plate`, or a `TipSpot` with its parent `TipRack`, merely for a downstream display. The
reference includes `ancestors` so a consumer can make that presentation choice without changing
the event's meaning.

Use `source` and `destination` only when the operation genuinely transfers a resource between
physical locations. Represent a resource endpoint with `resource_reference()`. Use
`coordinate_reference()` for a geometric endpoint when no PLR resource exists.

Avoid adding fields solely to simplify one consumer. An event should describe what the PLR API
actually did; dashboards, logs, and integrations can derive their own views from the structured
references.

## Operation templates

### Machine lifecycle

Use for `setup()` and `stop()` on a public PLR machine frontend.

```python
@evented_operation(
  "machine.setup",
  lambda self, **_: {"device": resource_reference(self), "resources": []},
)
async def setup(self, **backend_kwargs):
  ...
```

Current example: `legacy.machines.Machine.setup` and `.stop`.

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

Current examples:

- `legacy.storage.Incubator.fetch_plate_to_loading_tray`
- `legacy.storage.Incubator.take_in_plate`
- `legacy.liquid_handling.LiquidHandler.resource_pickup`, `.resource_move`, and `.resource_drop`

For pickup and drop, record the resource's invocation state in `.started`. A
`completed_data_factory` may capture its final assignment or pose for `.completed`.

### Liquid handling

Use the direct operated containers in `resources`, plus one `liquid_operations` record per
channel. Each item should include the channel, direct resource reference, owning plate reference
when applicable, and `volume_ul`.

```python
{
  "device": resource_reference(liquid_handler),
  "resources": [resource_reference(well)],
  "liquid_operations": [{
    "channel": 0,
    "resource": resource_reference(well),
    "plate": resource_reference(well.parent),
    "volume_ul": 50.0,
  }],
}
```

Current examples: `legacy.liquid_handling.LiquidHandler.aspirate` and `.dispense`.

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

Current examples: `LiquidHandler.pick_up_tips`, `.drop_tips`, `.pick_up_tips96`, and
`.drop_tips96`.

### Thermal and shaking operations

Represent the issuing controller as `device`. Include an operated resource only when the PLR API
has one. Use explicit unit-suffixed fields for operation parameters:

```python
{
  "device": resource_reference(controller),
  "resources": [],
  "temperature_c": 37.0,
  "duration_s": 300.0,
  "speed_rpm": 800.0,
}
```

Current examples:

- `legacy.shaking.Shaker.shake` and `.stop_shaking`
- `legacy.temperature_controlling.TemperatureController.set_temperature`,
  `.wait_for_temperature`, and `.deactivate`

### Arm/controller motion

Public controller operations should identify the controller in `device` and use explicit,
unit-bearing motion arguments where relevant. Low-level controller operations can be useful to a
diagnostic listener, but higher-level resource-aware wrappers should emit the resource-transfer
events when an arm is actually approaching, picking up, moving, or dropping a PLR resource.

Current examples: `brooks.precise_flex.PreciseFlex` lifecycle, joint/cartesian/rail/gripper
motion, pick/drop, and park operations.

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
5. Use explicit units in quantitative field names, such as `volume_ul`, `duration_s`, and
   `temperature_c`.
6. Include `source` and `destination` only for actual resource transfers.
7. Add tests for `.started`, `.completed`, and `.failed`, including operation-ID correlation.
8. Verify no EventBus listener is required for normal device operation and that listener failures
   cannot alter hardware control flow.

The first implementation instruments a limited set of shared and legacy frontends. New or
existing drivers should adopt these conventions incrementally at their public semantic API
boundaries.
