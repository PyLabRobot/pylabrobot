# How this integration is built

The patterns the Mantis integration is written to, so that a new feature can follow them and an
existing one can be checked against them. Each is stated as a rule, with where it is followed. The
last section lists what is open and where the code does not follow a rule yet.

## The layers

```
device.py                    MantisDevice, a Resource, and the Mantis() factory. setup() / stop(),
                             the driver, the parts; places the arm's links from a pose read.
driver/master.py             MantisDriver. Owns the link, the reader, reply matching, events,
                             the sequencer, discovery and the saved configuration; reads the
                             motors; halts the device at stop; shows setup and stop on the LED
                             bar.
driver/arm_kinematics.py     The arm's geometry and units: device units, inverse and forward
                             kinematics, the absolute encoder's correction. Pure maths.
driver/stage_transform.py    The stage transform: where a point on the wellplate is in the arm
                             frame. Pure maths.
driver/events.py             The events the device sends unasked: their codes, what each carries
                             and how it is logged.
driver/simulator.py          MantisSimulationDriver, MantisSimulationArm,
                             MantisSimulationPlateClamp, MantisSimulationPump, MantisSimulationRfid,
                             MantisSimulationLedBar. Answer from the declared configuration;
                             nothing reaches a wire.
driver/framing.py            The frame: header, check word, splitting, payload strings, boards.
driver/errors.py             MantisError and one error per kind; what the device's codes mean.
driver/configuration.py      DeviceConfiguration: what the device answered about itself.
driver/oem_config.py         The unit's own values, read from its vendor
                             .config and sequence files, into a declared configuration.
driver/sequence_files.py     The unit's sequence files: the waypoints of a route that moves the
                             arm.
driver/confirmed_firmware_versions.py
                             The firmware sets confirmed on a device, per generation.
driver/features/             One class per thing the device is fitted with: Arm, PlateClamp, Pump,
                             Rfid, LedBar.
driver/recordings/           One real Mantis of each generation: its configuration and unit values.
resource_model/              The parts the device is modelled as, their files and manifest.
liquid_classes/              MantisLiquidClass and mantis_mapping, by viscosity band and chip type.
```

## The patterns

**M1. One driver. Features hang off it.** (STAR P1.) A feature is a plain class holding `_driver`
and sending through it. No per-feature backend or frontend.

**M2. The generation comes from discovery.** `_request_configuration` reads the firmware version
on boards 0, 10, 11 and 12. Only board 0 must answer. Each other board gets one attempt, and one
that times out, refuses or answers a malformed version is absent, not a fault: it is logged and
recorded as `None`. Board 11 answering means v4; absent means v3. Board 0's version string and the
serial number decide nothing.

**M3. Nothing exists until the device says it is fitted.** (STAR P2.) `discover` reads the
configuration (`_request_configuration`), checks it against a declared one, then
`_discover_features` builds the features it lists; a refused setup builds none. The RFID reader
is the exception: `discover` builds it when it answers its ping on interface A, after every
opening of the link.
Each feature is an `Optional` attribute of the driver, `None` until `_discover_features` builds
it: `plate_clamp` is built only when board 12 answered discovery, so a v3 without it has none;
`pump` only when board 10 answered, releasing its reservoir only on a v4, whose install names the
valve, and tared and self-tested at setup (`tare_and_self_test`) only on a v4; `arm` only when
board 11 answered (a v4). `led_bar` is gated by the generation: built only on a v4 whose board 12
answered, never on a v3, whose install sets no LED count. A difference between v3 and v4 is
expressed there, by which feature is built and with what, never by a branch on the generation
inside a command.

**M4. A configuration holds facts only, and is data.** (STAR P3, P13.) `DeviceConfiguration` holds
what the device answered: generation, firmware versions, motor limits, and the pressure board's
sensor calibration (`feedback_sensor_parameters`), read at setup; on a v4 the tare then writes the
bottle's and chip's offsets, and the configuration holds what it wrote.
`save_configuration` writes it; `declared_configuration_json` reads one back and setup refuses if
the device disagrees on a fitted field (`FITTED_FIELDS`). The calibration is neither saved nor
declared: the bottle and chip sensors' offsets are re-zeroed on the device between sessions, so
they are read at every setup. A declared file that holds one, saved by an earlier version, loads;
it is ignored. A simulated pump answers the generation's recording's.
Each feature gets its own configuration when it is added, holding its own facts; the device
configuration does not grow per feature, the calibration excepted, as discovery reads it with the
firmware versions. Motor limits are compared as a build fact: every v4 is taken to share them until
a second device shows otherwise.

**M4a. A unit's own values come from its folder or its declared file, else its generation's
recording.** What is set per unit and cannot be read off the device sits in the unit's vendor
`Data/Device/Configs` folder (`oem_config_directory`). Each value is a block (`UNIT_BLOCKS`): where
the folder and the declared file both hold it they must agree, or setup refuses listing the
differences; one source suffices; with neither, the generation's recording supplies it, less
accurate on this unit. A file saved before a block or a non-fitted device field existed still loads:
each value it lacks comes from the folder, else the recording, in simulation too, with one warning
naming each and where it came from. A later block or field is optional the same way.
`save_configuration` saves the unit's blocks, never a recording's, so after one setup with the
folder the saved file alone carries them. A block is loaded only where its source applies:
`plate_clamp.levels` for a clamp driven as a DC motor, `rfid.slot_to_antenna` where `RFID.config`
exists, `led_bar.number_of_leds` where `Device.config` sets `NumberOfLed`, `arm` where the unit
declares the arms' absolute encoder references (a v4), its encoder correction from the `Data`
folder; `zones` where the unit declares its zones (a v4), with each zone's routes, and `stations`,
the routes to the waste and wash stations, from `System/Sequences/Common`; `stage` where the unit
declares its stage transform, with its tilt and arm type and the plate holder's origin from the
`Data` folder.

**M4d. Motion calibration never comes from a recording.** A block that calibrates motion
(`MOTION_CALIBRATION_BLOCKS`: `arm`, `zones`, `stations`, `stage`) comes from the unit's folder or
its declared file only; no recording holds one. Without it, nothing that needs it is sent: without
`arm` `Arm.initialize` refuses, and so does `setup` unless `skip_arm=True`; without `zones` the head
is moved to idle from near idle only; without `stations` a move to a station refuses; without
`stage` a move to a wellplate refuses. An arm type other than `Standard` is refused. A route file is
read whole and refused on any line it does not understand, never skipped. Factory files (encoder
corrections, taught sequences) are never committed; tests use synthetic tables.

Which vendor values are the unit's own, from a v3 and a v4 install side by side:

| values | file | |
|---|---|---|
| deck transform and tilt: `MicroplateTransformationMatrix`, `MicroplateOffsetX/Y` | `Device.config` | the unit's |
| arm offsets `Motor.1/2.Offset`; homing `Motor.1/2.HomingPositionN`, `HomingPositionA` (v4) | `RobotArm.config` | the unit's |
| absolute-encoder correction (v4) | `System/Encoder Correction/*.csv` | the unit's |
| plate holder origin | `Device/Adapters/SBS Adapter.ad.txt` | the unit's |
| chip changer and sequence safe Z (`ChipChanger.MovementZ`, `*.seq.txt`) | `ChipChanger.config`, sequences | unit or generation, unsettled |
| clamp levels, RFID antennas, LED count (`NumberOfLed`, v4) | `Device.config`, `RFID.config` | one unit seen; read as the unit's |
| deck reference points and heights, arm geometry, drop verification | `StageTuning.config`, `RobotArm.config`, `DropVerification.config` | the same on both units |
| chip catalogue and liquid classes | `System/`, `Device/Others/` | the software's; liquid classes ship as code (M4c) |

**M4b. Errors are one class per kind, under `MantisError`.** (STAR's errors by kind.) Each also
subclasses the built-in it corresponds to, so an `except RuntimeError`, `ValueError` or
`TimeoutError` still matches. A device code is carried on the error, and what it means is a table
(`MOTOR_ERROR_CODE_DESCRIPTIONS`, `CLAMP_STATE_DESCRIPTIONS`, ...), not a class per code. A state
the device reports as a failure raises at once, never after a full wait.

**M4c. Liquid classes ship as code.** (STAR's `liquid_classes/mapping.py`.) A liquid class is plain
data, one per (viscosity band, chip type) in `mantis_mapping`, taken from a v4 install's table,
which follows the software's chip catalogue, not the unit. A dispense takes one per call; its
explicit arguments win. No lookup by viscosity: the bands leave gaps (5.5 cP is in none).

**M5. One command in flight; replies match on packet number and board.** The reader runs from
setup to stop. A frame with a code from 0x0200 is an event: it is logged by its content
(`describe_event`; a code not decoded, such as 0x7FFF, by its number) and goes to every waiter
registered for its board and code whose `match` accepts its payload (`_expect_event`, before the
command that causes it is sent, then `_wait_for_event`). A motor-error event fails every waiter for
a motion event (`MOTION_EVENTS`) at once with `MantisMotionError`. An event whose payload does not
match its layout is dropped before any waiter sees it. A reply nobody is waiting for is logged and
dropped.
A size field no frame can have (`MAXIMUM_FRAME_SIZE`) drops one byte at a time until a frame
starts. A read that times out is resent with the same packet number, up to `attempts`. A command
that writes or moves is sent once (`attempts=1`), never resent. A failed read ends the reader: the
command in flight and every event waiter fail with it, and every later command, including one
already waiting for the lock, raises until setup runs again. `stop` fails the command in flight and
every event waiter. After a failed read, `stop` (and `setup`, which runs it first) sends nothing:
it closes the dead link with one warning.

**M5a. The sequencer plays queued items; a queue write is checked by its reply.** Clear (61),
start (62), stop (63) and abort (64) are `_unchecked_fw_*` calls, sent once, returning the reply's
word. `_queue_ppi_write` numbers each item from 1 to `LAST_QUEUE_ITEM` per opening of the link and
sends it once (`_unchecked_fw_queue_write_ppi`, command 65). A reply of -1 free slots means the
queue was full and the item not queued: it is sent again under the next item, for at most
`default_queue_full_timeout`; a non-zero error word raises `InvalidQueueItemError`.
`_wait_for_queue_item` waits for the item's progress event with nothing left queued; the reader
keeps the last such item, so a report that came with the reply is not missed. A move of all three
motors is queued the same way (`_unchecked_fw_queue_move_item`, command 68, `_queue_move_item`);
a motor sent all zeros does not move.
As seen on a v4: a duration is in steps of 1 ms. A sequencer not started takes 200 items
(`SEQUENCER_QUEUE_SIZE`), then answers -1; a clear frees the slots. A frame written twice under
one packet number is queued twice, so a queue write is never resent. A clear empties the queue
but does not stop a running sequencer. Stop lets the queue play out, then reports it stopped;
abort finishes the item playing, reports it stopped and keeps the rest for the next start. Only a
clear drops items. On an idle sequencer, stop and abort keep the queue and report nothing. Clear,
stop and abort answer 2 when sent while the sequencer runs, 0 when it is idle.
Every progress event from board 0 sets `_sequence_progress`, the item played last, which the LED
bar's dispense progress follows; opening the link clears it.

**M6. The wire speaks the device's units; reads return them.** (STAR P4.) Every read returns what
the device sent, decoded but not converted, until the feature that owns it knows the conversion.
A feature then converts in one place, on its configuration, and speaks PyLabRobot's units. No unit
suffix on names in default units. The arm converts in `arm_kinematics`: 200 device units per motor
revolution, degrees on the arms and mm on Z (down from its flag), each rate rounded up half a step
as on the wire. A joint position keeps the motor's sign (Z down from its flag); a Cartesian height,
a location's `z` or a traverse height, is mm above Z's home flag, up positive, converted inside. The
routes keep the frame their files write: the arm frame, Z down from its flag. A point on a
wellplate goes through the stage transform (`stage_transform`) in full: y flipped to run from the
wellplate's back edge, the height taken below `REFERENCE_HEIGHT`, the arm type, the holder's origin,
the projective matrix on x and y and the tilt on z; never a fitted homography.

**M7. A range is checked before anything is sent.** (STAR P5.) A read accepts only the indices
seen on a device (`MOTORS`, `ABSOLUTE_ENCODER_MOTORS`, the boards, `PRESSURE_CONTROLLERS`);
`_check_index`, and the pump's `_get_controller_index`, raise `ValueError` before a frame is built.

**M8. A command that changes the device has one raw call and one public method.** (STAR P7, P8,
P9.) The raw call is `_unchecked_fw_*` and sends once; the public method checks its arguments, then
confirms what the device reports. `lock` checks the level against the declared levels, calls
`_unchecked_fw_do_plate_clamping` and reads the clamp state until it reports clamping done.
`Arm.initialize` matches the recorded v4 startup command for command: energise the arms, declare
them three times where their absolute encoders read, set their following error, clear Z, then
recover to idle. Recovering declares the arms, moves them along the head's zone's route to its safe
XY, homes Z (waiting for its home-done event), twice declares them and moves along the zone's route
to idle, and homes Z; the zone is read again before each route, from the controller's position
just declared. `recover_from_fault` stops every motor, clears Z's faults then the arms', and
recovers the same way, as in a recorded recovery after a motor error. A route moves each motor its
line names on its own (command 22), as in the recorded recoveries; every target is checked first. A
command 22 answers a motor error code, and one not 0 raises `MantisMotionError` before the next
move is sent. A move reads each motor's status until none moves; a fault bit raises
`MantisMotionError`. Routes move at the `ROUTE_*` profile, the only one they were recorded at; the
`default_*` speeds are for the other moves. Idle is
`POST_HOMING_POSITION` (the same on every unit seen) less the unit's offsets; a route line's
(x, y) goes through the same inverse kinematics, so a line at (15, 31.180) is sent as such.

The arm's own moves check every target against the motor limits read, and Z's speed and acceleration
against `Z_SPEED_RANGE` and `Z_ACCELERATION_RANGE`, before anything is sent. `move_z` is one command
22; `move_to_joint_position` and `move_through_waypoints` are queued moves (command 68), a motor not
named held, the sequencer cleared and started before them and the last waited for;
`move_to_location` rises to `minimum_traverse_height` only if its encoder reads it below (command
22), reads Z back at it by its encoder (command 84, within `Z_TRAVERSE_TOLERANCE`) or raises, moves
the arms with Z held (queued), then lowers Z (command 22). `park` rises and reads Z back the same
way, then follows the zone's route to idle. Neither models the chip racks: the arms' move is one
joint move. A station's route is queued line for line, as in the recorded move to the waste station,
and starts only from idle (`request_is_parked`, Z read by its encoder).

`move_to_resource_location` takes a wellplate or a resource on one: the head goes over the
resource's centre `PITCH_HEIGHT` above the wellplate's tallest point (its frame, a well or its lid),
the dispense clearance, plus `offset`, in the wellplate's frame, and moves as
`move_to_location`. Its traverse height is mm above the wellplate's base, not Z's home flag, by
default the same `PITCH_HEIGHT` above that point; a traverse below it, a wellplate turned on its
holder or a resource on no wellplate is refused before anything is sent.

`start_pump` turns the pump on and enables each controller at its last target;
`start_pressure_control` sets one target and reads the controller's status until it reports
settled, or raises `MantisPressureError`; `tare_pressure_sensors` refuses with the pump on, vents
each positive line and writes its offset less the vented reading. `Pump.initialize` tares, then
runs the connect self-test write for write as in the recorded v4 startup, each block running the
pump only while its controllers are enabled.

**M9. Names say what kind of thing they are.** (STAR P10.)

| what it touches | prefix | here |
|---|---|---|
| the device, doing work | `move_` | `arm.move_z`, `arm.move_to_location` |
| the device, a process left running | `start_`, `stop_` | `pump.start_pump`, `pump.stop_pump` |
| the device's sensors | `measure_`, `sense_` | `pump.measure_pressure` |
| the device's memory, reading | `request_` | `request_motor_limits`, `request_motor_status` |
| the device's memory, writing | `set_` | |
| the resource model, reading | `get_` | |
| the resource model, writing | `update_` | |

Commands, boards and fields are named for what they do; no vendor class, method or opcode names.

**M10. What is acquired is released on every path.** A feature that turns on or holds anything
(pump, pressure controllers, clamp, a chip) turns it off or releases it in a `finally`, each step in
its own `try`, and the first fault is the one raised. `stop` fails what waits on the link; on a v4
it aborts the sequencer, stops each motor at its default deceleration and, if every motor stopped,
parks an arm brought up: from away from idle first along its zone's route; then Z up on its own,
read back by its encoder (command 84), never the demand position (command 27); then queued, as in
the recorded close, the arms, both, then Z down to `SHUTDOWN_Z` at idle. Then it turns the pump off
and vents the bottle and chip lines (`Pump._halt`, in a `finally` after the motion); then it
releases each built feature, turns the LED bar off and closes the link. A move that fails part way
stops the motors it sent a move and logs where their encoders read. `start_pump` and `stop_pump`
are counted; the last stop disables every controller, turns the pump off and releases the
reservoir, every step whatever the one before did, then raises the first fault; only `Pump._halt`
logs a fault and goes on. A failed start, and a failed pump setup, turn the pump off before
raising, and opened valves are closed on every path. A `lock` that does not report clamping done
releases the clamp before raising, and `stop` releases a clamp that does not read released. A
failed `Arm.initialize` or `recover_from_fault` unpowers the arm motors again, and a failed setup's
`stop` does not park them. Queued moves not reported played abort the sequencer, so nothing more
plays, and log where the motors stopped. Failure behaviour is designed before coverage.

**M11. Firmware is confirmed, never gated.** A version set outside `CONFIRMED_FIRMWARE_VERSIONS`
warns once at setup; nothing refuses. A set goes in once it has been seen on a device.

**M12. Defaults are public `default_*` attributes on the feature.** (STAR P18.) Declared on the
class with a type, in PyLabRobot units, round, slightly below the firmware's own. A caller tunes
one device by assigning on the instance, or every device by assigning on the class; a command
checks the value it takes before anything is sent. `plate_clamp.default_lock_level` is the first;
`pump.default_settle_timeout` and `pump.default_vent_duration` are checked when assigned.

**M13. Tests assert the wire, against real replies.** (STAR P15.) A test pins the bytes we send. A
reply in a test is one a device sent, held as a named constant in the test, never rebuilt or
invented. The test link answers by board, command and payload and echoes the packet number; only
the framing tests pin whole recorded frames. A reply that changes with what was sent before is a
recorded list the test plays in order. A test exists where behaviour could regress.

**M14. Comments say what this code does and what the device did.** (STAR P17.) No capture names,
serials or dates; a constant is referenced by name, not repeated.

**M15. The simulator answers from the declared configuration; nothing reaches a wire.** (CLARIOstar
simulator.) `MantisSimulationDriver` builds no link and `_send_command` raises. Setup takes the
configuration from the file's `device` block (a field it lacks filled as in M4a), and the reads
answer from it; a board it leaves `None` does not answer, and the device name is not simulated. Each
feature has a simulation subclass that overrides its `_unchecked_fw_*` calls and reads with state
above the wire: `MantisSimulationPlateClamp` reads 0 until a command sets clamping done or
unclamping done; `MantisSimulationRfid` reads every slot empty; `MantisSimulationPump` holds the
pump and each controller as last commanded, off after setup: a regulated line reads its target and
settled, any other 0; its holds pass at once, its tare writes nothing and the calibration is the
recorded one. `MantisSimulationLedBar` takes every command and waits no hold.
`MantisSimulationArm` stands at idle with its absolute encoders reading there, so it needs no
encoder correction; its offsets are the declared ones, else 0, and its zones, stations and stage
the declared ones, else none. Each command moves it at once, and the motor reads answer from it;
without it the motors read idle at 0, as after power-on. A motor read the configuration does not
hold is not simulated. The sequencer, on the driver, holds `SEQUENCER_QUEUE_SIZE` items, stopped
and empty when the link opens and started by the pump's setup on a v4, and once started plays each
item at once, a queued move moving the simulated arm; stop and abort keep what is queued.

**M16. Locking is by subsystem.** (STAR P11.) One lock serializes the link today. When features
add commands that the device runs together, each subsystem gets its own lock, taken in declaration
order.

**M17. The master holds every link to the device.** (STAR P21.) The FTDI has two interfaces; the
master opens both in `setup` and closes them in `stop`, and a feature sends only through the
master's links. Interface B carries the command frames; interface A, the RFID reader's commands.

**M18. The resource model mirrors the device; it never decides for it.** (STAR P12.) `MantisDevice`
builds the parts and holds the driver; a feature reaches the parts through the device, and moves
keep them in step. A driver with no resource tree still drives. The Z column carries the arm's
four links (`LinkBody`), turned to the joint angles setup reads after the bring-up.

**M19. The LED bar shows what the device does, never at the cost of it.** An action runs inside
`_show_action`: the bar shows its colour, the enclosing action's when it ends, the idle animation
when none is left, and `error_color` after one that failed. Each colour is an attribute of the bar
(`setup_color`, ...), checked when it is set and read when it is shown; each bar has its own.
`show_progress` lights a share of the LEDs from the left in `dispense_progress_color` and sends
nothing when that many are already lit: each frame holds the link about 62 ms on a v4, so an action
shows at most `number_of_leds` frames. Setup shows blue, the arm's bring-up and the pump's setup
with it, the connect self-test's progress one step per test point, then green for
`SETUP_DONE_HOLD`, then idle; `stop` turns the bar off and shows no colour first: one sent there was
replaced within milliseconds, too briefly to see. The arm's moves and the pump's start, stop,
pressure and vent show no colour of their own. An LED bar fault only warns: it never fails or masks
the action. `show_actions=False` shows no action, only the idle animation from setup on, and no
progress. A colour change costs about 0.1 s of link time (two commands), so an action that is
quick on its own shows none: the plate clamp's lock and unlock, where a colour took a release from
0.21 s to 0.34 s on the recorded v4.

## Open, and where this is not consistent yet

1. **`CommandRefusedError` is untested.** No reply seen has a non-zero status.
2. **The v3 board set is an inference.** No v3 has run discovery, so whether a v3 lacks board 11,
   or answers board 10 or 12, is unproven.
3. **Three link values are ours, not the vendor's.** The latency timer (2 ms), the read poll
   (10 ms) and the reply timeout (5 s). A frame whose reserved word is not zero is logged, not
   refused.
4. **The arm model is partial.** The links are drawn as boxes: the manufacturer's link models are
   compressed in a form not converted yet. The Z column is drawn with Z at 0: how far it travels
   with Z is not measured. Which side of the device motor 0's links sit on is taken from the name
   of a link length in the unit's files, not seen on a device. The motor reads stay on the driver
   in the device's units; the arm converts them.
5. **Whether motor limits differ between devices of one generation** is unknown; one v4 has been
   read.
6. **The plate clamp and the RFID reader have run on one v4 only,** on macOS and on Windows. No v3
   has run this driver.
7. **The release payload is fixed, not declared.** Every release sends (0.75, 0, 5000); how it
   relates to `Device.config`'s `PlateClampUnclampPWM` (1000) is not known.
8. **A lock that times out releases (command 31).** Command 32 may stop a lock instead, but no
   capture has a command 32 frame, so the release, seen on the wire, is kept.
9. **`MantisDevice` writes its base file's `mesh` itself** rather than through
   `resource_model._mesh`, which is private to that package.
10. **A clamp failure state (4, 7 or 8) raises at once, untested.** No device has shown one, so
    no test plays one.
11. **A v3's clamp is pneumatic, switched by aux output 2 on the pressure board.** A v3 install's
    `Device.config` reads `Pneumatic`, `PressureRegulator`, aux 2, with no PWM levels; #987 switches
    aux output 2 off at shutdown. PR 1 builds no clamp on a v3; the v3 clamp comes next.
12. **Capturing both interfaces at once is unproven.** They share one serial number, and a PLR
    capture tells links apart by it.
13. **The antenna switch's address on a v3 is unknown.** The PPI write at 120 is seen only on our
    v4; board 0's PPI also drives the valves. A v3 capture of an RFID scan settles it before a v3
    reads.
14. **The RFID multiplexer delay is taken as ms.** Every unit seen holds 0, so the unit of
    `RfidMultiplexerDelay` is unconfirmed.
15. **The liquid classes' units are unconfirmed.** The delays may be ms or the valve sequence's time
    steps, the pressures psi and the vacuum inHg; the dispensing PR settles them.
16. **Some motor read fields are named by their order only.** The three currents, and the
    configuration's flag and three values, have no meaning confirmed on the wire; the
    position read returns its first field, the demand position, and drops the five after it.
17. **Each arm motor's first absolute encoder read after connecting is not valid,** on both
    motors of the v4. It is read again, up to `ABSOLUTE_ENCODER_READS` (5, ours); the recorded v4
    startup reads it twice. Why it is not valid is not known.
18. **The vacuum sensor's offset reads about -38.8,** against about -13.6 on the bottle and chip
    sensors. The tare rewrites the bottle's and chip's only; the vacuum's is not tared. The target
    pressure read (command 10) is left out: no reply to it has been recorded.
19. **A v3's pressure board is unproven.** Setup reads the calibration from any board 10 that
    answers its version; one that does not answer that read leaves it empty, with a warning.
20. **The LED bar has run on one v4 only.** The `fatal_error` and `unconnected` animations have
    never been sent to a device, nor yellow shown; only writes of all 20 LEDs are recorded, so
    `set_color` takes one colour per LED, no fewer.
21. **The LED bar's replies are not checked.** A colour write and an animation start answer 1
    when taken; a colour write while an animation plays answers 0 and is not shown, as in the
    recorded v4, so every colour write stops the animation first.
22. **The dispense progress has no caller yet.** `_show_fill` (`show_progress`), `_follow_fill` and
    `_show_dispense_done` wait for the dispense; `_follow_fill` reads `_sequence_progress`, which
    the sequencer sets.
23. **A clear's words 8 and 9 are not explained.** A clear answered 8 once, right after Z homed,
    and 9 has been seen; 2 and 0 mean running and idle. The word is returned, not checked.
24. **`InvalidQueueItemError` is untested.** No queue reply seen has a non-zero error word.
25. **A motor error is not kept for a later wait.** One that comes after an item is queued but
    before `_wait_for_queue_item` begins leaves that wait to time out; the sequencer stops, so the
    item is never reported played.
26. **A sequence-stopped event's second word is not confirmed.** It read 0 after a stop that let
    the queue play out and 2 after an abort that left two items queued, as if it counts them.
27. **The arm's bring-up and stop have not run through this driver.** Their commands and order
    match the recorded v4 startup and close; a device run settles the rest. Before it parks, stop
    waits for the motors to stop, refuses on a fault and reads where the head is from the absolute
    encoders; no fault is cleared, so whether a stopped motor plays a queued move is not seen. Z
    rises on its own before the arms move, unlike the recorded close's one queue; it must read
    within `Z_TRAVERSE_TOLERANCE` (0.1 mm) of its flag by its encoder, twice the largest gap read
    between Z's encoder and its demand position. Z's encoder has been read at rest only, never right
    after a move.
28. **Without the unit's zones, the arm is moved to idle only from near idle.** Setup, `park`,
    `recover_from_fault` and stop then move to idle only with the head within `FRONT_ZONE_X_RANGE`
    (the Front zone's x bounds), along the Front zone's routes, the same on every unit seen;
    elsewhere they refuse. With the zones, the head's zone names its routes.
29. **The absolute encoder's wrap is not observed.** A motor's position is taken one span lower
    above 100 (motor 0) or 120 (motor 1) device units; no reading near a wrap has been seen. Of the
    forward kinematics' two solutions the head's is the one left of the line between the elbows:
    the recorded recoveries from four start poses each followed the zone it gives, and from one
    (a left zone) the solution of greater y would have chosen a right zone. No recovery from a
    back zone, where the two differ most, has been recorded.
30. **A slip before Z homes refuses.** If an arm reads a following error just before Z homes,
    setup raises instead of homing; no recording shows what should follow.
31. **Unpowering the arm (command 19, open loop) is unrecorded.** A failed bring-up sends it; its
    reply has not been seen.
32. **The encoder correction files and the sequences are found two levels above
    `oem_config_directory`, the plate holder's origin one level above.** That argument still names
    `Data/Device/Configs`; they sit in `Data/System` and `Data/Device/Adapters`.
33. **Zones that share an edge are tried in `ZONES` order.** A head on an edge goes to the first
    zone listed that holds it, in the order the unit lists its zones; the tie-break is not
    known.
34. **None of the moves, routes or stations has run through this driver.** Their wire form matches
    recorded exchanges: a recovery's routes as command 22 per motor, the waste station as queued
    moves line for line, each matched against a recording; a `park` from any zone also uses
    command 22, as the recovery does, since no park at a pause or a stop has been recorded.
35. **`park` rises to the traverse height first, and a station starts from idle only.** Neither is
    recorded: the recorded routes move X and Y at the height the head is at, and the recorded
    station routes start with the arms' move to the ready position. Both are this driver's
    guards, as STAR's park lifts before it travels.
36. **Z's speed and acceleration ranges are those of the recorded moves, not the device's.** 1 and
    5 mm/s have been seen accepted; only a move above the range has been seen dropped.
37. **`recover_from_fault` stops each motor at its default deceleration.** In a recorded recovery
    each was stopped at the acceleration of its last move; the order of stops, clears and
    declarations is the recorded one.
38. **The waste station's route is the generation's own file.** Every unit seen has both
    `MoveToWasteStation` and `MoveToWasteStation_Adapter`, two different routes: a unit with zones
    (a v4) reads the first, which a v4 played on the wire; one without them (a v3) reads the
    second, not seen played, and a v3 builds no arm. The wash routes are
    `MoveToWashStation1` and `2`; their `Lc3` variants are not read.
39. **Moves do not turn the resource model's links.** `MantisDevice` places them from a read after
    setup only.
40. **`request_is_parked`'s tolerance, 0.5 degrees and 0.5 mm, is ours.** In the recordings the
    declared arms read up to about 0.1 degrees from their idle target after a move there.
41. **A command 22 answering a motor error code is untested.** Every reply seen is 0; no test
    plays another. The check reads the code only; commands 19, 21 and 24 answer one too, unchecked.
42. **The read-back after a rise is Z's encoder (command 84), not the demand position.** It
    catches a rise the device refused, dropped or stalled, within `Z_TRAVERSE_TOLERANCE`; Z's
    encoder has been read at rest only, never right after a move.
43. **The stage transform has not commanded a move through this driver.** The same chain
    reproduces, to 1 µm, the recorded head positions over the 96 wells of recorded dispenses on a
    v4; that check needs the unit's own matrix and origin, so it is kept outside this repository
    and the tests here are synthetic. Whether the nozzle stands over each well is for a device run
    to show.
44. **The wellplate's back-left corner is taken as the holder's datum,** as the clamp pushes it
    there, with A1 at the back left: y runs from the wellplate's own back edge. Where the resource
    model places the wellplate is not used; one turned on its holder is refused.
45. **A height is in the wellplate frame.** Its z starts `REFERENCE_HEIGHT` above the wellplate's
    base, at the stage origin's z; how far a chip's nozzle, or the head without one, then stands
    above the wellplate is not measured. Hence the default clearance of `PITCH_HEIGHT`, never 0.
46. **The traverse height is converted at the target's x and y.** The tilt makes it differ by up
    to about 0.25 mm across a wellplate; the head rises only if below it, as `move_to_location`.
47. **The stage's x and y offsets keep their x and y, unused.** Only their z tilts the stage;
    the recorded x and y were reproduced without them.
48. **The arm moves to a wellplate in one joint move,** as `move_to_location`: nothing models the
    chip racks, so it runs only with the path between the head and the wellplate clear.
49. **The pump, the tare, the self-test and the vent have not run through this driver.** Their
    writes match the recorded v4 startup write for write, the tare's offsets byte for byte for the
    same readings, and the vent the one a v4 has run; a device run settles the rest.
50. **The self-test gives no verdict.** A test point that does not settle is logged; the install's
    spec values are not used until they are confirmed.
51. **The tare, settle and self-test values are the install's, as constants.** They are the same
    in the v3 and v4 installs seen, the test points' order aside; they are not read per unit.
52. **A tare whose readings do not agree within 5 s writes nothing.** Ours: a line not steady when
    vented is not taken as atmospheric.
53. **A v3's pump is not set up.** Setup neither tares nor runs the self-test on a v3, and its pump
    releases no reservoir, its install naming none; stop still turns the pump off and vents valves
    0 and 1, not seen on a v3.
54. **The vacuum is neither vented nor read at stop,** as in the recorded close; the v4 reads
    about -11 inHg after a stop. No aux output is written on a v4: the recorded close sends none.
55. **The pump starts at the self-test's last targets** (bottle and chip 20 psi, vacuum -15 inHg)
    after a setup, a skipped self-test included; a target `start_pressure_control` sets replaces
    its line's.
56. **The pump stops at once.**
57. **`start_pressure_control`'s ranges are not the device's.** No range of the device's is
    known. The bottle and chip take 0 to 20 psi, the self-test's highest point; the install's
    bottle maximum (5) is not enforced, as the recorded self-test sends the bottle 20. The vacuum
    takes -16 to 0 inHg, the deepest a liquid class sets; no recorded run went below -15 inHg, the
    self-test's deepest, so -16 is unconfirmed on the device.
58. **The self-test's solenoid toggles hold nothing between writes.** The recorded ones take about
    1 s each on the wire, though the install's toggle interval is 200 ms; the writes are the same.
