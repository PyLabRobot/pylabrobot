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

- `@evented_operation(...)` for a trivial projection of one public async method's invocation.
- `with event_operation(...):` for explicit semantic context assembled inside an operation.
- `emit_event(...)` only for a meaningful state transition that is not an operation lifecycle.

Use a low-level diagnostic event only when the transport boundary itself is useful to observe.
Diagnostic events may inherit the enclosing semantic operation context, but do not define a new
protocol-level action or resource-transfer meaning.

## Choosing an instrumentation style

Prefer explicit `event_operation()` construction for new semantic frontend operations. It keeps
the event boundary and metadata next to the code that determines their meaning:

```python
async def move_to_target(self, requested_target: str) -> None:
  target = self.resolve_target(requested_target)
  target_coordinate = self.coordinate_for_target(target)

  with event_operation(
    "plate_mover.move_to_target",
    device=resource_reference(self),
    resources=[],
    requested_target=requested_target,
    target=target,
    target_coordinate=coordinate_reference(target_coordinate),
  ):
    await self.backend.move_to(target_coordinate)
```

Compute metadata before entering the operation scope when doing so has no hardware side effects.
Perform operation validation inside the scope when validation failures are part of the operation's
lifecycle. Always enter the scope before issuing hardware commands so command failures emit the
correlated `.failed` event. If meaningful data is known only after successful execution, expose it
with `completed_data_factory`; return the full completed-event payload, including stable invocation
context:

```python
operation_data = {
  "device": resource_reference(self),
  "resources": [resource_reference(plate)],
}
completion_data: dict[str, object] = {}
with event_operation(
  "reader.read_plate",
  **operation_data,
  completed_data_factory=lambda: {**operation_data, **completion_data},
):
  result = await self.backend.read_plate(plate)
  completion_data["result"] = result
```

The completion factory runs after the operation body has succeeded. Keep it pure, deterministic,
and non-throwing so event construction cannot turn a successful hardware operation into an
application failure.

`@evented_operation(...)` remains appropriate when all event metadata is a simple, pure projection
of invocation arguments and pre-operation resource state. Its context factory is called with the
method's original `*args` and `**kwargs`; the decorator does not inspect, bind, normalize, or apply
defaults to the call. The context factory must therefore mirror the decorated method's calling
signature, including positional order, parameter kinds, defaults, and `**backend_kwargs`:

```python
def _set_temperature_event_context(
  self: "TemperatureController",
  temperature: float,
  passive: bool = False,
) -> dict[str, object]:
  return {
    "device": resource_reference(self),
    "resources": [] if self.resource is None else [resource_reference(self.resource)],
    "target_temperature": temperature,
    "passive": passive,
  }


@evented_operation("temperature_controller.set_temperature", _set_temperature_event_context)
async def set_temperature(
  self,
  temperature: float,
  passive: bool = False,
) -> None:
  ...
```

Do not use a decorator context factory when event meaning depends on validation, normalization,
resolved targets, derived values, hardware responses, or final resource state. Construct the event
explicitly inside the operation instead. In either style, context construction must be pure: it
must not command hardware, mutate resource state, or perform expensive I/O.

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

## Canonical operation schemas

The [Event Schema Registry](event-schemas.md) defines the canonical operation names, fields,
units, lifecycle-specific payloads, and resource semantics for every currently instrumented
frontend and diagnostic producer. Use an existing schema whenever the operation meaning matches.

If a contribution introduces a genuinely new device or operation family, choose semantically
accurate conventions, add the proposed contract to the registry in the same contribution, and
treat maintainer review as establishing the standard for future implementations of that operation.
Do not create an undocumented vendor-local alias for an existing concept.

### Manual operator actions

Manual operations use `manual_operator.<action>` so the event records both the manual executor and
the semantic work requested. Represent the `ManualOperator` as `device`, list any direct modeled
resources in `resources`, and preserve action-specific request data without substituting inferred
deck resources. A genuine manual resource transfer additionally includes its actual `source` and
`destination` resource references.

```python
{
  "device": device_reference(manual_operator, name=manual_operator.name),
  "resources": [resource_reference(plate)],
  "manual_action": "centrifuge.spin",
  "title": "Spin sample plate",
  "details": {
    "relative_centrifugal_force_g": 300,
    "duration_seconds": 180,
  },
}
```

Operator cancellation or a provider-reported failure is a failed lifecycle outcome. Completion
metadata such as `confirmed_by` belongs only on the `.completed` event.

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
3. Prefer explicit `event_operation()` construction, especially when context includes validated,
   normalized, resolved, derived, measured, or final-state values.
4. Use `@evented_operation(...)` only for a trivial invocation projection. Match the context
   factory's complete calling signature to the decorated method; the decorator forwards the
   original `*args` and `**kwargs` without binding or normalization.
5. Include `device` and direct `resources` in the operation context.
6. Preserve PLR resource semantics; use ancestry for context rather than substituting resources.
7. Use PLR's default units without repeating them in field names. Add a suffix only when a value
   deliberately uses a different unit or representation, such as `speed_rpm` or `speed_pct`.
8. Include `source` and `destination` only for actual resource transfers.
9. Add tests for `.started`, `.completed`, and `.failed`, including operation-ID correlation. For
   decorated methods, test both positional and keyword invocation when the public method supports
   both.
10. Verify no EventBus listener is required for normal device operation and that listener failures
    cannot alter hardware control flow.
11. Update the [Event Schema Registry](event-schemas.md) and user-guide implementation matrix when
    adding a new operation, changing a payload, or instrumenting a new frontend.

New or existing drivers should adopt these conventions incrementally at their public semantic API
boundaries. A new operation family establishes precedent for future devices, so its schema should
be reviewed as deliberately as its implementation.
