# How this integration is built

The patterns the STAR integration is written to, so that a new feature can follow them and an
existing one can be checked against them. Each is stated as a rule, with where it is followed. The
last section lists where it is not, which is what this document is for.

## The layers

```
device.py           STARDevice, a Resource. Its deck is its child, so the device and everything
                    on it is one tree. Frame factories: STAR / STARlet / STARPlus.
driver/master.py    STARDriver. Owns the link, the reply router, the subsystem lock, discovery,
                    initialization order, the saved configuration, and the resource model.
driver/features/    One class per thing the device is fitted with: pipettes, head96, head384,
                    iswap, autoload, x_arm, cover. Each holds a driver and its own configuration.
driver/simulator.py A subclass per feature that answers where the wire would.
driver/errors.py    One exception class per error code, raised off the reply.
driver/lock.py      Which commands may be in flight together.
resource_model/     The resources a feature is modelled as, built by the driver at setup.
recordings/         What a real device answered, as data. See its own README.
```

The transport, the framing and the reply router are shared with other Hamilton devices and live in
`pylabrobot/hamilton/`, not here.

## The patterns

**P1. One driver. Features hang off it.** A feature is a plain class holding `_driver` and sending
through `driver.send_command`; the only base class is `Head`, which the 96- and 384-heads share.
There is no per-feature backend and no separate frontend: the driver is the whole of it, and the
device layer above adds only what the driver cannot know, which is where things are.

**P2. Nothing exists until the device says it is fitted.** Every feature is an `Optional`
attribute, built in `STARDriver.discover` from the configuration bits the device answered. The
device's accessors read through to the driver's. A feature already built is kept, so a caller can
hand one its configuration before setup and re-running setup does not throw it away.

**P3. Every feature has a `.configuration` dataclass, and it holds facts only.** Three kinds, in
this order: what the device answered about itself, per-machine calibration, and the device facts of
that generation of drive - encoder resolutions, the ranges each drive accepts, and the firmware's
own defaults. What the driver chooses to send is not a fact about the device and does not go here
(P18). `DeviceConfiguration` in `driver/configuration.py` is the same thing for the device.

**P4. The wire counts in increments; this driver speaks mm, degrees, uL and seconds.** Every
conversion is a method on the configuration - `x_increments_to_mm` / `x_mm_to_increments` - so the
resolution has one home. No conversion is written inline, and no method takes a `_mm` suffix,
because mm is the default.

**P5. A range is `(lowest, highest)`, and it is checked before anything is sent.** Refusals raise
`ValueError` naming the range and the value; a precondition that has not been read yet raises
`RuntimeError` saying so. Never `assert`, which is stripped under `-O`. `_check_reachable(axis,
value)` is the single gate every position passes through, one per feature. A check that asks a
different question composes on top of it and is named for what it checks rather than for
reachability: `_check_move` for a whole move's parameters, `_check_tip_command` for one command's
narrower window, `_check_pose_reachable` for the pose two joint angles would produce. Preconditions
are `_require_*`.

**P6. Every firmware parameter is an argument, and its default is written out.** A command that
takes a speed, an acceleration and a current limit exposes all three, defaulted from the feature's
`default_*` attributes (P18), or its configuration's until it moves over, rather than left to
whatever the drive would have used, so a run records what it asked for.

**P7. `_unchecked_fw_*` is the raw wire call.** It assembles and sends, and does nothing else.
Unchecked means unchecked *by this driver*: the arguments are not guarded and the model is not
updated. The device still does whatever it does, including sensing. The public method above it
guards, decides which firmware command the move should use, and records.

**P8. The device is the authority on where it is.** A move records what was asked for as soon as
the command answers, and then reads the drive back and records that, in a `finally`, so a command
that failed part way still leaves the model where the drive actually stopped. `update_*` writes
what was asked; `_record_where_it_stopped` writes what is. Followed by `head`, `head96`, `iswap`,
`autoload`, and `x_arm` on its failure path only, because its settle reads already record the arm
on the way out.

**P9. One way to move each component.** Each drive has exactly one method that commands it, with
one `try`/`finally` and one model update. Anything more convenient is a helper on top of that
method, never a second path to the same drive.

**P10. Names say what kind of thing they are.** The prefixes follow the PLR command prefix
proposal, which splits them by what is on the other end:

| what it touches | prefix | here |
|---|---|---|
| the machine, doing work | `move_`, `aspirate_`, `dispense_`, `pickup_`, `shake_` | `move_to_y_position`, `move_to_safe_z` |
| the machine, a process started and left running | `start_`, `stop_` | `start_tadm_monitoring`, `stop_tadm_monitoring` |
| the machine's sensors | `read_`, `capture_`, `measure_`, `sense_` | `sense_tip_presence`, `sense_carrier_presence_on_deck` |
| the machine's memory, reading | `request_` | `request_y_positions`, `gripper_request_width` |
| the machine's memory, writing | `set_` | `set_drive_parameter`, `set_loading_indicators` |
| the resource model, reading | `get_` | `elbow_drive_get_angle`, `wrist_drive_get_angle` |
| the resource model, writing | `update_`, `assign_`, `unassign_` | `elbow_drive_update_angle`, `update_location_by_reference_point` |

`get_` and `request_` are not the same question: `get_` asks this driver's model, `request_` asks
the device. `set_` only writes device memory: a process started and left running is a `start_` /
`stop_` pair, as in `start_temperature_control` / `stop_temperature_control` and `start_shaking` /
`stop_shaking`. On a feature with several drives the drive's name comes first: `wheel_*`,
`scanner_*`, `gripper_*`, `elbow_*`, `wrist_*`. Arguments are ordered as the execution uses them,
not by type. `discover` / `initialize` / `park` are the lifecycle and take no prefix.

**P11. Locking is by subsystem, not by module.** The master routes: `C0 DI` drives the channels and
`C0 II` the autoload, and the device runs those together. So a command names the subsystem it
drives and each subsystem has one mutex, with two wider scopes for the commands that reach further.
Read-only `R` and `Q` commands take no lock. Locks are always taken in declaration order, so two
callers cannot deadlock.

**P12. The resource model mirrors the device; it never decides for it.** `resource_model/` builds
the resources, the driver's `_create_*_resources` hangs them off the deck at setup, and moves keep
them in step. A driver given no deck models nothing and still drives.

**P13. A configuration is data, not code.** `save_configuration` writes what a device answered;
`declared_configuration_json` reads one back, and is the only path a configuration takes off a
file. Against a simulated device it stands in for the device; against a physical one it is
cross-checked against what answers, on what is fitted and how much of it, and setup refuses if they
disagree. Values that come from a drive's documentation rather than from a device stay in the code.
The ones the firmware version decides are listed in the class's `firmware_variable` and written in
a `firmware_variable` block beside the fields: shown, never read back, and checked against the code.

**P14. The simulator overrides only what would reach the wire.** One subclass per feature, each
implementing `answer` from the model; everything above it - discovery, the initialization order,
the configuration each feature resolves - runs exactly as it does against hardware. It replaces
`_send`, below `send_command`, so the subsystem locks still serialize what a real run serializes.

**P15. Tests assert the wire.** A test replaces `send_command`, drives the public method, and
compares the assembled command string. What the device would have answered is a table in the test.

**P16. One exception class per error code.** `check_fw_string_error` raises off the reply, so an
error is raised where the command was sent rather than inspected by the caller.

**P17. Comments and docstrings describe this code and what the rig did.** A claim about how the
machine is built belongs in one only when a device, a log or a wire capture established it. Numbers
that a constant already holds are referenced by name rather than repeated.

**P18. The driver's defaults are public `default_*` attributes on the feature.** Declared on the
class with a type, in standard units, round, and slightly below the firmware's own default. A
caller tunes one device by assigning on the instance, or every device by assigning on the class.
Named `default_<quantity>[_<condition>]`: `default_y_speed`, `default_z_speed`.

**P19. A probe names its speeds for what they do.** `search_speed` is the speed it searches at;
`approach_speed` is the speed to where the search starts, where the command has one. Never a bare
`speed`, on the probe or on its `_unchecked_fw_` command. The Prep's probes follow the same rule.

**P20. A command over several channels plans above and sends below.** The public method plans the
work into the fewest commands the channels can carry out at once and calls `_<verb>_in_one_move`
for each; that method takes one list that goes in one command, sends it, handles a command that
stopped part way, and records what the device says. Splitting is by what the firmware cannot do in
one command: `C0 TP` and `C0 TR` carry an X per channel, so X never splits a batch, while one tip
type per command, one collar height per drop, and two channels closer in Y than they may stand all
do. A tip command travels as high as the mounted tip allows, its stop disc an overhang above that,
and never below the 245.0 mm safety height. `pick_up_tips` and `drop_tips` follow this, and so do
the Prep's.

## Where this is not consistent yet

1. **`.configuration` has two owners.** Most features own theirs
   (`self.configuration = configuration or XConfiguration()`). `XArm.configuration` is a property
   over `DeviceConfiguration.left_arm` / `.right_arm`, because the device reports both arms in one
   reply. It reads and writes through, so the constructor takes a configuration like every other
   feature's, but there is still nowhere to put one before the device has been read: an arm asked
   to take a configuration before setup raises instead. That is the half of P2 an arm cannot do.

2. **`FrontCover` has no `discover`,** because there is nothing to read: it has no module of its
   own and reports no firmware version. It is also the only file in the package carrying a
   commented-out command set, waiting on a device to say whether those commands exist.

3. **`Head384` inherits everything and reads nothing.** No `discover`, no guards of its own. Its
   device facts are documented defaults rather than readings. Nothing is wrong with it until a
   384-head is read.

4. **`driver/__init__.py` re-exports two error classes and not `STARDriver`,** while
   `driver/features/__init__.py` and `resource_model/__init__.py` export their whole surface and
   `star/__init__.py` exports the device, the driver and the simulator. Any of these is defensible;
   the four together are not a convention.

5. **One prefix is used that P10's table does not name.** `probe_*` (`probe_z_max`,
   `gripper_probe_for_object`) is a measurement taken by moving, which the proposal has no prefix
   for - it is neither a plain `move_` nor a `sense_` that leaves the device where it stands. The
   simulator's `_modelled_y`, `_modelled_z` and `_modelled_track` are the same question for a
   private helper, where the table does not reach.

6. **One reading is filed as memory rather than measurement.**
   `gripper_request_latest_force_applied` reads the force sensor, which P10's table puts under
   `measure_`; legacy called it `measure_iswap_gripper_force`. It is `request_` here because the arm
   answers it out of the registers it recorded the last movement in, not from the sensor at the
   moment of asking.

7. **`DeviceConfiguration` documents its fields by wire parameter and bit number**
   (`"Bit 1: ISWAP. False = none, True = installed."`), which no feature configuration does. It is
   the oldest of the configurations and reads like the document it was transcribed from rather than
   like the rest of them.

8. **The driver's defaults still live in the configurations.** `Head`, `Head384`, `iSWAP`,
   `Autoload` and `XArm` keep them as `*_default` / `*_default_increments` fields, some in
   increments, and `Pipettes` keeps a few there. `Head96` and `CoreGrippers` follow P18; the rest
   move over one feature at a time.
