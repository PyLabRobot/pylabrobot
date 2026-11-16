# Shaker and environmental-controller events

Each listed semantic operation emits `started`, `completed`, or `failed` lifecycle records.

## Shaker

The instrumented `legacy.shaking.Shaker` frontend emits:

| Operation | Primary fields |
| --- | --- |
| `shaker.shake` | `device`, loaded `resources`, `speed_rpm`, optional `duration` |
| `shaker.stop_shaking` | `device`, loaded `resources` |

## Temperature controllers

Instrumented temperature-control frontends emit the operations their public APIs support. The
legacy `TemperatureController` does not have a separate activation method; direct frontends such as
the HighRes sample stores do.

| Operation | Primary fields |
| --- | --- |
| `temperature_controller.set_temperature` | `device`, loaded `resources`, `target_temperature`, `passive` |
| `temperature_controller.activate` | `device`, loaded `resources` |
| `temperature_controller.wait_for_temperature` | `device`, loaded `resources`, `target_temperature`, `timeout`, `tolerance`; completed event adds `current_temperature` |
| `temperature_controller.hold_temperature` | `device`, loaded `resources`, `duration`, configured `target_temperature` when known |
| `temperature_controller.deactivate` | `device`, loaded `resources`, configured `target_temperature` when known |

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

## Humidity and gas controllers

Environmental-control frontends use the same set/activate/deactivate lifecycle for humidity, CO2,
and O2. Targets are fractions: `0.90` means 90% RH and `0.05` means 5% gas concentration.

| Operation | Primary fields |
| --- | --- |
| `humidity_controller.set_humidity` | `device`, `resources`, `target_humidity` |
| `humidity_controller.activate` | `device`, `resources` |
| `humidity_controller.deactivate` | `device`, `resources` |
| `co2_controller.set_co2` | `device`, `resources`, `target_co2` |
| `co2_controller.activate` | `device`, `resources` |
| `co2_controller.deactivate` | `device`, `resources` |
| `o2_controller.set_o2` | `device`, `resources`, `target_o2` |
| `o2_controller.activate` | `device`, `resources` |
| `o2_controller.deactivate` | `device`, `resources` |

The HighRes sample stores currently emit these operations for their installed controllable
channels. See the [device event reference](../../high_res/sample-storage/events.md) for model
coverage.
