# Hettich robotic centrifuge events

Each semantic operation emits `started`, `completed`, or `failed` lifecycle records. Hettich uses
the same canonical centrifuge operation name and common payload fields as other PyLabRobot
centrifuge frontends, including VSpin.

| Operation | Primary fields |
| --- | --- |
| `centrifuge.spin` | `device`, empty `resources`, empty `bucket_resources`, `speed_rpm`, `duration`, optional `relative_centrifugal_force` |

The driver reports the requested rotor speed as `speed_rpm`. If it was constructed with a supported
`rotor_catalog_number`, it also calculates and reports `relative_centrifugal_force`. The Hettich
frontend does not currently model rotor positions as PLR resource holders, so both resource lists
are empty.
