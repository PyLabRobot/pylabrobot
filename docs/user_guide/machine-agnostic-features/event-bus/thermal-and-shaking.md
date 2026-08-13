# Shaker and temperature-controller events

Each listed semantic operation emits `started`, `completed`, or `failed` lifecycle records.

## Shaker

The instrumented `legacy.shaking.Shaker` frontend emits:

| Operation | Primary fields |
| --- | --- |
| `shaker.shake` | `device`, `speed_rpm`, optional `duration_s` |
| `shaker.stop_shaking` | `device` |

## TemperatureController

The instrumented `legacy.temperature_controlling.TemperatureController` frontend emits:

| Operation | Primary fields |
| --- | --- |
| `temperature_controller.set_temperature` | `device`, `target_temperature_c`, `passive` |
| `temperature_controller.wait_for_temperature` | `device`, `target_temperature_c`, `timeout_seconds`, `tolerance_c` |
| `temperature_controller.deactivate` | `device` |

An operated resource appears only when the corresponding PLR call directly acts on one.
