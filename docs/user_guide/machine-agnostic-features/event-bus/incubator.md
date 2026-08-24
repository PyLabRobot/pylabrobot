# Incubator events

The instrumented `legacy.storage.Incubator` frontend emits the following semantic operations:

| Operation | Lifecycle events | Primary fields |
| --- | --- | --- |
| `incubator.fetch_plate` | `started`, `completed`, `failed` | `device`, `resources`, `source`, `destination` |
| `incubator.take_in_plate` | `started`, `completed`, `failed` | `device`, `resources`, `source`, `destination` |

`resources` contains the direct moved plate. `source` and `destination` identify the relevant
PLR holders, such as a storage site and loading tray. The completed event can reflect the plate's
post-operation resource assignment.
