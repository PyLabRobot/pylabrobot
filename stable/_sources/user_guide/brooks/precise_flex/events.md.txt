# PreciseFlex events

The instrumented `brooks.precise_flex.PreciseFlex` frontend emits `started`, `completed`, and
`failed` records for each of these public controller operations:

| Category | Operations |
| --- | --- |
| Lifecycle and state | `precise_flex.setup`, `stop`, `power_on`, `power_off`, `recover_from_fault`, `home`, `start_freedrive`, `stop_freedrive`, `halt` |
| Motion | `move_to_joint_position`, `move_to_location`, `move_through_cartesian_poses`, `move_gripper`, `move_gripper_joint_position`, `move_rail`, `park` |
| Controller pick/drop | `pick_up_at_joint_position`, `drop_at_joint_position`, `pick_up_at_location`, `drop_at_location` |

Each event includes the controller `device`. Cartesian targets are reported as `target.location`
using PLR's serialized `Coordinate` form. Joint targets use an axis-name-to-value mapping.

These controller-level operations do not invent a PLR resource transfer. A resource-aware caller
that knows it is approaching, picking up, moving, or dropping a PLR resource should emit the
corresponding resource-transfer operation separately.
