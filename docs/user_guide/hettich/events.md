# Hettich robotic centrifuge events

Each semantic operation emits `started`, `completed`, or `failed` lifecycle records. Hettich uses
the same canonical centrifuge operation name and common payload fields as other PyLabRobot
centrifuge frontends, including VSpin.

| Operation | Primary fields |
| --- | --- |
| `centrifuge.spin` | `device`, empty `resources`, empty `bucket_resources`, `relative_centrifugal_force`, `duration` |

The driver reports the requested `g` as `relative_centrifugal_force`, in multiples of standard
gravity (× g), including when the value was calculated with `rpm_to_g()`. Conversion to the
device's integer RPM happens internally; the event preserves the requested force without
rounding it to the achievable RPM. The Hettich frontend does not currently model rotor positions
as PLR resource holders, so both resource lists are empty.
