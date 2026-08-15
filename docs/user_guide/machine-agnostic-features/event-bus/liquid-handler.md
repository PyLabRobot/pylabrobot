# LiquidHandler events

The instrumented `legacy.liquid_handling.LiquidHandler` frontend emits these semantic operations.
Each operation emits `started`, `completed`, or `failed` lifecycle records.

| Operation | Primary fields |
| --- | --- |
| `liquid_handler.resource_pickup` | `device`, direct moved `resources`, source holder when assigned |
| `liquid_handler.resource_move` | `device`, direct moved `resources` |
| `liquid_handler.resource_drop` | `device`, direct moved `resources`, `destination` |
| `liquid_handler.tip_pickup` | `device`, direct `resources`, `tip_operations` |
| `liquid_handler.tip_drop` | `device`, direct `resources`, `tip_operations` |
| `liquid_handler.tip_pickup_96` | `device`, direct `resources`, `tip_operations` |
| `liquid_handler.tip_drop_96` | `device`, direct `resources`, `tip_operations` |
| `liquid_handler.aspirate` | `device`, direct `resources`, `liquid_operations` |
| `liquid_handler.dispense` | `device`, direct `resources`, `liquid_operations` |

`liquid_operations` carries a record for each channel with `channel`, direct `resource`, optional
owning `plate`, and `volume`. `tip_operations` similarly carries each channel and direct tip
location. The direct tip or well is never substituted with a parent rack or plate; structural
ancestors are available in the resource reference when needed.

For `resource_pickup`, `source` is the direct parent holder captured before PLR unassigns the
resource after successful hardware pickup. It is omitted if the resource was already unassigned.
