# Machine lifecycle events

The instrumented `legacy.machines.Machine` frontend emits the following semantic operations:

| Operation | Lifecycle events | Primary fields |
| --- | --- | --- |
| `machine.setup` | `started`, `completed`, `failed` | `device`, `backend` |
| `machine.stop` | `started`, `completed`, `failed` | `device`, `backend` |

`device` is the issuing PLR machine. `backend` is the backend class name. Child frontends may
emit additional semantic or diagnostic events while setup or shutdown is in progress.
