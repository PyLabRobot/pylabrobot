# Incubator events

The instrumented `legacy.storage.Incubator` and HighRes sample-storage frontends emit the following
semantic operations where their public APIs support them:

| Operation | Lifecycle events | Primary fields |
| --- | --- | --- |
| `incubator.fetch_plate` | `started`, `completed`, `failed` | `device`, `resources`, `source`, `destination` |
| `incubator.take_in_plate` | `started`, `completed`, `failed` | `device`, `resources`, `source`, `destination` |
| `incubator.transfer_plate` | `started`, `completed`, `failed` | `device`, `resources`, `source`, `destination` |

`resources` contains the direct moved plate. `source` and `destination` identify the relevant
PLR holders, such as a storage site and loading tray. The completed event can reflect the plate's
post-operation resource assignment.

`incubator.transfer_plate` describes a direct move between two transfer nests or other incubator
endpoints. The HighRes-specific environmental events are documented in its
[sample-storage event reference](../../high_res/sample-storage/events.md).
