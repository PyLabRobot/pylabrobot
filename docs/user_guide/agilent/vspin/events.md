# VSpin and Access2 events

Each listed semantic operation emits `started` followed by either `completed` or `failed`.
The modern Agilent frontends and resource-aware legacy `Centrifuge` and `Loader` frontends
share spin and transfer schemas. The setup/stop operations below belong to the modern
Agilent frontends; legacy machine lifecycle events retain their separate schema.

## VSpin centrifuge

| Operation | Primary fields |
| --- | --- |
| `centrifuge.setup` | `device` |
| `centrifuge.stop` | `device` |
| `centrifuge.spin` | `device`, loaded `resources`, `bucket_resources`, `relative_centrifugal_force`, `duration`, `acceleration_fraction`, `deceleration_fraction` |

`VSpin.setup()` connects, initializes, homes, and positions the centrifuge. `VSpin.stop()`
closes its transport; it is not `stop_spin()` or an emergency stop. Both lifecycle operations
identify the VSpin with `device_reference(self, name=self.name)`, without a legacy `backend` field.

`resources` includes each plate currently loaded in a VSpin bucket when the spin starts.
`bucket_resources` preserves which physical bucket holds each plate. The event reports the
requested cycle parameters. `relative_centrifugal_force` is the dimensionless multiple of
standard gravity conventionally written as x g, PLR's default unit for relative centrifugal
force. The event does not infer an actual measured force or completed duration outside the
frontend call's success or failure lifecycle.

## Access2 loader

| Operation | Primary fields |
| --- | --- |
| `centrifuge_loader.setup` | `device` |
| `centrifuge_loader.stop` | `device` |
| `centrifuge_loader.load` | `device`, moved `resources`, `source`, `destination` |
| `centrifuge_loader.unload` | `device`, moved `resources`, `source`, `destination` |

`centrifuge_loader.load` moves a plate from the Access2 staging holder to the VSpin bucket at the
load position. `centrifuge_loader.unload` moves a plate in the reverse direction. `source` and
`destination` are the actual PLR holders involved in the transfer.

Use `await loader.setup()` and `await loader.stop()` on the `Access2` frontend. They delegate
to `Access2Driver.setup()` and `Access2Driver.stop()` with unchanged hardware behavior, and
identify the Access2 holder with `resource_reference(self)`. Direct driver methods remain
available but emit no semantic setup/stop events, preventing duplicate lifecycles. These
frontend events do not use `machine.setup`/`machine.stop` or require a `backend` field.
