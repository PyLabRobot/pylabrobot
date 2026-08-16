# Operator actions

`ManualOperator` lets a protocol await work performed by a person without coupling the protocol
to a terminal, notebook, graphical interface, or external task system.

```python
from pylabrobot.manual_operator import ConsoleOperatorActionProvider, ManualOperator

operator = ManualOperator(ConsoleOperatorActionProvider(), name="cell_operator")

await operator.perform(
  action="centrifuge.spin",
  title="Spin sample plate",
  instructions="Spin plate_1 at 300 x g for 180 seconds, then return it to the handover nest.",
  details={
    "relative_centrifugal_force": 300,
    "duration": 180,
  },
)
```

The built-in console provider treats Enter as a successful acknowledgement. Applications can
implement `OperatorActionProvider` to present the same request through a graphical interface,
HTTP service, LIMS, or message broker:

```python
from pylabrobot.manual_operator import OperatorActionRequest, OperatorActionResult


class DashboardOperatorActionProvider:
  async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
    result = await dashboard.publish_and_wait(action)
    return OperatorActionResult.completed(confirmed_by=result.user)
```

Providers return one of three explicit outcomes:

- `completed`: `perform()` returns the result and the protocol continues.
- `cancelled`: `perform()` raises `OperatorActionCancelledError`.
- `failed`: `perform()` raises `OperatorActionFailedError`.

An acknowledgement records what the operator reported. It does not prove that the physical work
occurred. Protocol-specific validation and resource-model reconciliation should happen before or
after `perform()` as appropriate.

## Moving a resource

Use `move_resource()` when the manual action transfers a modeled PLR resource between two modeled
locations:

```python
await operator.move_resource(
  resource=sample_plate,
  source=centrifuge_loader,
  destination=handover_nest,
)
```

The method validates the source and destination before prompting, but leaves the resource assigned
to its source while the operator works. After the provider reports completion, it validates the
model again and assigns the resource to the destination using PLR's normal resource-assignment
machinery. A `ResourceHolder` supplies its normal child location; other destinations can use an
explicit `destination_location=Coordinate(...)`.

Cancellation, reported failure, and provider exceptions do not modify the resource model. If the
model changes while the operator request is pending, the method raises an error rather than
overwriting the newer state.

## EventBus integration

When an EventBus subscriber is active, each awaited action emits one correlated lifecycle using
the requested action as its semantic subtype:

```text
manual_operator.centrifuge.spin.started
manual_operator.centrifuge.spin.completed
```

The event identifies the `ManualOperator` as `device`, includes any direct PLR `resources` passed
to `perform()`, and preserves the request's title, instructions, confirmation text, and structured
details. The completed event adds `confirmed_by` and the provider's result message when supplied.
Cancellation, reported failure, invalid provider results, and provider exceptions emit `.failed`
with the normal EventBus error fields.

Use stable action identifiers such as `centrifuge.spin`, `plate_reader.read`, or
`quality_control.inspect`. When the manual action has an automated counterpart, use that
operation's canonical field names and PLR default units inside `details`. `move_resource()` emits
`manual_operator.resource.move.*` with the direct moved resource plus its true `source` and
`destination` resource references. The normal
`resource.unassigned` and `resource.assigned` state-transition events record the subsequent model
update.
