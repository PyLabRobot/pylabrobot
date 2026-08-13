# Agilent VSpin and Access2 events

Each listed semantic operation emits `started`, `completed`, or `failed` lifecycle records.

## VSpin centrifuge

| Operation | Primary fields |
| --- | --- |
| `centrifuge.spin` | `device`, loaded `resources`, `bucket_resources`, `relative_centrifugal_force_g`, `duration_seconds`, `acceleration_fraction`, `deceleration_fraction` |

`resources` includes each plate currently loaded in a VSpin bucket when the spin starts.
`bucket_resources` preserves which physical bucket holds each plate. The event reports the
requested cycle parameters; it does not infer an actual measured force or completed duration
outside the frontend call's success or failure lifecycle.

## Access2 loader

| Operation | Primary fields |
| --- | --- |
| `centrifuge_loader.load` | `device`, moved `resources`, `source`, `destination` |
| `centrifuge_loader.unload` | `device`, moved `resources`, `source`, `destination` |

`centrifuge_loader.load` moves a plate from the Access2 staging holder to the VSpin bucket at the
load position. `centrifuge_loader.unload` moves a plate in the reverse direction. `source` and
`destination` are the actual PLR holders involved in the transfer.
