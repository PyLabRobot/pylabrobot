# Centrifuge and Access2 events

Each listed semantic operation emits `started`, `completed`, or `failed` lifecycle records. The
modern Agilent and Hettich frontends and the resource-aware legacy `Centrifuge` and `Loader`
frontends use the same canonical operation names and payload semantics.

## VSpin centrifuge

| Operation | Primary fields |
| --- | --- |
| `centrifuge.spin` | `device`, loaded `resources`, `bucket_resources`, `relative_centrifugal_force`, `duration`, `acceleration_fraction`, `deceleration_fraction` |

`resources` includes each plate currently loaded in a VSpin bucket when the spin starts.
`bucket_resources` preserves which physical bucket holds each plate. The event reports the
requested cycle parameters. `relative_centrifugal_force` is the dimensionless multiple of
standard gravity conventionally written as x g, PLR's default unit for relative centrifugal
force. The event does not infer an actual measured force or completed duration outside the
frontend call's success or failure lifecycle.

## Hettich robotic centrifuges

| Operation | Primary fields |
| --- | --- |
| `centrifuge.spin` | `device`, empty `resources`, empty `bucket_resources`, `speed_rpm`, `duration`, optional `relative_centrifugal_force` |

Hettich uses the same `centrifuge.spin` lifecycle and common field names as VSpin. The driver
reports the requested rotor speed as `speed_rpm`. If it was constructed with a supported
`rotor_catalog_number`, it also calculates and reports `relative_centrifugal_force`. The Hettich
frontend does not currently model rotor positions as PLR resource holders, so both resource lists
are empty.

## Access2 loader

| Operation | Primary fields |
| --- | --- |
| `centrifuge_loader.load` | `device`, moved `resources`, `source`, `destination` |
| `centrifuge_loader.unload` | `device`, moved `resources`, `source`, `destination` |

`centrifuge_loader.load` moves a plate from the Access2 staging holder to the VSpin bucket at the
load position. `centrifuge_loader.unload` moves a plate in the reverse direction. `source` and
`destination` are the actual PLR holders involved in the transfer.
