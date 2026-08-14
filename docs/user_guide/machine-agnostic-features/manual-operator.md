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
    "relative_centrifugal_force_g": 300,
    "duration_seconds": 180,
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
