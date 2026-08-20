# Operator actions

`ManualOperator` lets a protocol await work performed by a person without coupling the protocol
to a terminal, notebook, graphical interface, or external task system.

## Why use a manual operator?

Many protocols start with a direct `input()` call. That is a reasonable choice for a simple
notebook pause, but `ManualOperator` gives the same physical handoff a reusable PLR contract:

- **Provider independence:** keep the protocol code unchanged while presenting the request through
  terminal input, a notebook, a custom GUI, LIMS, Slack, or another acknowledgement system.
- **Explicit outcomes:** providers report `completed`, `cancelled`, or `failed` rather than
  reducing every acknowledgement to Enter being pressed.
- **Resource-model reconciliation:** `move_resource()` validates a manual transfer before and
  after acknowledgement, then applies the corresponding PLR assignment only when the model is
  still consistent.
- **Native traceability:** an active EventBus receives a correlated lifecycle with the action,
  affected resources, endpoints, operator acknowledgement, and failure details where applicable.

The built-in console provider is intentionally simple. It is a practical default for users who
only need an interactive pause; more capable providers are optional application integrations.

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

`OperatorActionRequest` carries any direct modeled `resources` plus optional `source` and
`destination` endpoints. Providers that cross a process boundary can serialize those objects in
their own transport format; `details` remains for operation-specific request data.

For a complete notebook example using a chatterbox incubator, manual plate transfer, plate reader,
and optional EventBus subscriber, see the
[ManualOperator Jupyter cookbook](../../cookbook/manual_operator_jupyter.ipynb).

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
from pylabrobot.resources import Rotation

await operator.move_resource(
  resource=sample_plate,
  source=centrifuge_loader,
  destination=handover_nest,
  destination_rotation=Rotation(z=0),
)
```

The method validates the source and destination before prompting, but leaves the resource assigned
to its source while the operator works. After the provider reports completion, it validates the
model again and assigns the resource to the destination using PLR's normal resource-assignment
machinery. A `ResourceHolder` supplies its normal child location; other destinations can use an
explicit `destination_location=Coordinate(...)`. Pass an explicit
`destination_rotation=Rotation(...)` when the manual move changes orientation. The rotation is
never inferred from the destination holder and is applied before a holder calculates its child
location.

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
`destination` resource references; do not mirror those endpoints as free-form `details`. The normal
`resource.unassigned` and `resource.assigned` state-transition events record the subsequent model
update.
