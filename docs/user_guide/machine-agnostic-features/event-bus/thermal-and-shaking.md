# Shaker and temperature-controller events

Each listed semantic operation emits `started`, `completed`, or `failed` lifecycle records.

## Shaker

The instrumented `legacy.shaking.Shaker` frontend emits:

| Operation | Primary fields |
| --- | --- |
| `shaker.shake` | `device`, loaded `resources`, `speed_rpm`, optional `duration` |
| `shaker.stop_shaking` | `device`, loaded `resources` |

## TemperatureController

The instrumented `legacy.temperature_controlling.TemperatureController` frontend emits:

| Operation | Primary fields |
| --- | --- |
| `temperature_controller.set_temperature` | `device`, loaded `resources`, `target_temperature`, `passive` |
| `temperature_controller.wait_for_temperature` | `device`, loaded `resources`, `target_temperature`, `timeout`, `tolerance`; completed event adds `current_temperature` |
| `temperature_controller.hold_temperature` | `device`, loaded `resources`, `duration`, configured `target_temperature` when known |
| `temperature_controller.deactivate` | `device`, loaded `resources` |

`hold_temperature` records a protocol-requested dwell while the controller remains at its existing
configuration. It does not send a new temperature command and does not assert that a resource has
reached the configured target.

For `wait_for_temperature`, `current_temperature` is the controller's final sensor reading that
satisfied the requested tolerance. It is emitted only on successful completion and does not imply
that a loaded resource itself reached that temperature.

If a resource is assigned when an operation starts, the event's `resources` contains that direct
loaded resource. If the holder is empty, `resources` is omitted rather than inferred from
surrounding deck state.

New direct temperature-controller frontends, such as vendor-specific Inheco frontends, should
implement this semantic EventBus contract at their own public API boundary. They do not need to
inherit from the legacy `TemperatureController` class.
