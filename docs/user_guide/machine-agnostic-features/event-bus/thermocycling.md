# Thermocycler events

The instrumented `legacy.thermocycling.Thermocycler` frontend emits one semantic lifecycle for each
primitive operation below.

| Operation | Primary fields |
| --- | --- |
| `thermocycler.open_lid` | `device`, optional loaded `resources` |
| `thermocycler.close_lid` | `device`, optional loaded `resources` |
| `thermocycler.set_block_temperature` | `device`, optional loaded `resources`, `target_temperatures` |
| `thermocycler.set_lid_temperature` | `device`, optional loaded `resources`, `target_temperatures` |
| `thermocycler.deactivate_block` | `device`, optional loaded `resources` |
| `thermocycler.deactivate_lid` | `device`, optional loaded `resources` |
| `thermocycler.run_protocol` | `device`, optional loaded `resources`, `block_max_volume`, `stage_count`, `step_definition_count`, `step_execution_count`, optional `temperature_zone_count` |

When a plate or other resource is assigned to the thermocycler at operation start, `resources`
contains that direct loaded resource. The field is omitted when the holder is empty.

`target_temperatures` is an ordered list with one temperature per zone, in PLR's default
temperature unit (degrees Celsius). `block_max_volume` uses the default volume unit (microliters).

`thermocycler.run_protocol` records a bounded structural summary instead of serializing the full
protocol:

- `stage_count` is the number of stages.
- `step_definition_count` is the number of distinct step definitions across the stages.
- `step_execution_count` includes each stage's repeats.
- `temperature_zone_count` is included when it can be derived from the protocol.

A `thermocycler.run_protocol.completed` event means that the backend coroutine returned
successfully. The frontend API can enqueue a profile and return before the physical temperature
program finishes, so this event must not be interpreted as physical profile completion. The full
protocol, its individual temperatures and hold times, backend return values, and backend-only
keyword arguments are not included.

## Deliberate exclusions

`run_pcr_profile` is a composite convenience method, so it has no separate parent lifecycle. Its
instrumented primitive calls, including `set_lid_temperature` and `run_protocol`, emit their normal
events without a synthetic parent operation identifier.

Status queries, `wait_for_block`, `wait_for_lid`, and `wait_for_profile_completion` are not yet
instrumented. Their polling and completion semantics need to be stabilized before they can define
portable EventBus operations.
