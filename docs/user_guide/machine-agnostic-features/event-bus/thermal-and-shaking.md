# Shaker and temperature-controller events

Each listed semantic operation emits `started`, `completed`, or `failed` lifecycle records.

## Shaker

The instrumented `legacy.shaking.Shaker` frontend emits:

| Operation | Primary fields |
| --- | --- |
| `shaker.shake` | `device`, loaded `resources`, `speed_rpm`, optional `duration_seconds` |
| `shaker.stop_shaking` | `device`, loaded `resources` |

## TemperatureController

The instrumented `legacy.temperature_controlling.TemperatureController` frontend emits:

| Operation | Primary fields |
| --- | --- |
| `temperature_controller.set_temperature` | `device`, loaded `resources`, `target_temperature_c`, `passive` |
| `temperature_controller.wait_for_temperature` | `device`, loaded `resources`, `target_temperature_c`, `timeout_seconds`, `tolerance_c` |
| `temperature_controller.hold_temperature` | `device`, loaded `resources`, `duration_s`, configured `target_temperature_c` when known |
| `temperature_controller.deactivate` | `device`, loaded `resources` |

`hold_temperature` records a protocol-requested dwell while the controller remains at its existing
configuration. It does not send a new temperature command and does not assert that a resource has
reached the configured target.

If a resource is assigned when an operation starts, the event's `resources` contains that direct
loaded resource. If the holder is empty, `resources` is omitted rather than inferred from
surrounding deck state.

New direct temperature-controller frontends, such as vendor-specific Inheco frontends, should
implement this semantic EventBus contract at their own public API boundary. They do not need to
inherit from the legacy `TemperatureController` class.
