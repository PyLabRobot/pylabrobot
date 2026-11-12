# How this integration is built

The patterns the Mantis integration is written to, so that a new feature can follow them and an
existing one can be checked against them. Each is stated as a rule, with where it is followed. The
last section lists what is open and where the code does not follow a rule yet.

## The layers

```
device.py                    MantisDevice, a Resource, and the Mantis() factory. setup() / stop(),
                             the driver, the parts.
driver/master.py             MantisDriver. Owns the link, the reader, reply matching, events,
                             discovery and the saved configuration.
driver/simulator.py          MantisSimulationDriver, MantisSimulationPlateClamp,
                             MantisSimulationRfid. Answer from the declared configuration; nothing
                             reaches a wire.
driver/framing.py            The frame: header, check word, splitting, payload strings, boards.
driver/errors.py             MantisError and one error per kind; what the device's codes mean.
driver/configuration.py      DeviceConfiguration: what the device answered about itself.
driver/oem_config.py         The unit's own values, read from its vendor
                             .config files, into a declared configuration.
driver/confirmed_firmware_versions.py
                             The firmware sets confirmed on a device, per generation.
driver/features/             One class per thing the device is fitted with: PlateClamp, Rfid.
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
it: `plate_clamp` is built only when board 12 answered discovery, so a v3 without it has none. A
difference between v3 and v4 is expressed there, by which feature is built, never by a
branch on the generation inside a command.

**M4. A configuration holds facts only, and is data.** (STAR P3, P13.) `DeviceConfiguration` holds
what the device answered: generation, firmware versions, motor limits. `save_configuration` writes
it; `declared_configuration_json` reads one back and setup refuses if the device disagrees on a
fitted field (`FITTED_FIELDS`). Each feature gets its own configuration when it is added, holding
its own facts; the device configuration does not grow per feature. Motor limits are compared as a
build fact: every v4 is taken to share them until a second device shows otherwise.

**M4a. A unit's own values come from its folder or its declared file, else its generation's
recording.** What is set per unit and cannot be read off the device sits in the unit's vendor
`Data/Device/Configs` folder (`oem_config_directory`). Each value is a block (`UNIT_BLOCKS`): where
the folder and the declared file both hold it they must agree, or setup refuses listing the
differences; one source suffices; with neither, the generation's recording supplies it with a
warning, less accurate on this unit. `save_configuration` saves the unit's blocks, never a
recording's, so after one setup with the folder the saved file alone carries them. A block is
loaded only where its source applies: `plate_clamp.levels` for a clamp driven as a DC motor,
`rfid.slot_to_antenna` where `RFID.config` exists.

Which vendor values are the unit's own, from a v3 and a v4 install side by side:

| values | file | |
|---|---|---|
| deck transform and tilt: `MicroplateTransformationMatrix`, `MicroplateOffsetX/Y` | `Device.config` | the unit's |
| arm offsets `Motor.1/2.Offset`; homing `Motor.1/2.HomingPositionN`, `HomingPositionA` (v4) | `RobotArm.config` | the unit's |
| absolute-encoder correction (v4) | `System/Encoder Correction/*.csv` | the unit's |
| plate holder origin | `Device/Adapters/SBS Adapter.ad.txt` | the unit's |
| chip changer and sequence safe Z (`ChipChanger.MovementZ`, `*.seq.txt`) | `ChipChanger.config`, sequences | unit or generation, unsettled |
| clamp levels, RFID antennas | `Device.config`, `RFID.config` | one unit seen; read as the unit's |
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
setup to stop. A frame with a code from 0x0200 is an event: it goes to every waiter registered for
its board and code (`_expect_event`, before the command that causes it is sent, then
`_wait_for_event`), and is logged either way. A reply nobody is waiting for is logged and dropped.
A size field no frame can have (`MAXIMUM_FRAME_SIZE`) drops one byte at a time until a frame
starts. A read that times out is resent with the same packet number, up to `attempts`. A command
that writes or moves is sent once (`attempts=1`), never resent. A failed read ends the reader: the
command in flight and every event waiter fail with it, and every later command, including one
already waiting for the lock, raises until setup runs again. `stop` fails the command in flight and
every event waiter.

**M6. The wire speaks the device's units; reads return them.** (STAR P4.) Every read returns what
the device sent, decoded but not converted, until the feature that owns it knows the conversion.
A feature then converts in one place, on its configuration, and speaks PyLabRobot's units. No unit
suffix on names in default units.

**M7. A range is checked before anything is sent.** (STAR P5.) A read accepts only the indices
seen on a device (`MOTORS`, the boards); `_check_index` raises
`ValueError` before a frame is built.

**M8. A command that changes the device has one raw call and one public method.** (STAR P7, P8,
P9.) The raw call is `_unchecked_fw_*` and sends once; the public method checks its arguments, then
confirms what the device reports. `lock` checks the level against the declared levels, calls
`_unchecked_fw_do_plate_clamping` and reads the clamp state until it reports clamping done.

**M9. Names say what kind of thing they are.** (STAR P10.)

| what it touches | prefix | here |
|---|---|---|
| the device, doing work | `move_` | |
| the device, a process left running | `start_`, `stop_` | |
| the device's sensors | `measure_`, `sense_` | |
| the device's memory, reading | `request_` | `request_motor_limits`, `request_name` |
| the device's memory, writing | `set_` | |
| the resource model, reading | `get_` | |
| the resource model, writing | `update_` | |

Commands, boards and fields are named for what they do; no vendor class, method or opcode names.

**M10. What is acquired is released on every path.** A feature that turns on or holds anything
(pump, pressure controllers, clamp, a chip) turns it off or releases it in a `finally`, each step
in its own `try`, and the first fault is the one raised. `stop` fails what waits on the link, then
releases each built feature, then closes the link. A `lock` that does not report clamping
done releases the clamp before raising, and `stop` releases a clamp that does not read released.
Failure behaviour is designed before coverage.

**M11. Firmware is confirmed, never gated.** A version set outside `CONFIRMED_FIRMWARE_VERSIONS`
warns once at setup; nothing refuses. A set goes in once it has been seen on a device.

**M12. Defaults are public `default_*` attributes on the feature.** (STAR P18.) Declared on the
class with a type, in PyLabRobot units, round, slightly below the firmware's own. A caller tunes
one device by assigning on the instance, or every device by assigning on the class; a command
checks the value it takes before anything is sent. `plate_clamp.default_lock_level` is the first.

**M13. Tests assert the wire, against real replies.** (STAR P15.) A test pins the bytes we send. A
reply in a test is one a device sent, held as a named constant in the test, never rebuilt or
invented. The test link answers by board, command and payload and echoes the packet number; only
the framing tests pin whole recorded frames. A reply that changes with what was sent before is a
recorded list the test plays in order. A test exists where behaviour could regress.

**M14. Comments say what this code does and what the device did.** (STAR P17.) No capture names,
serials or dates; a constant is referenced by name, not repeated.

**M15. The simulator answers from the declared configuration; nothing reaches a wire.** (CLARIOstar
simulator.) `MantisSimulationDriver` builds no link and `_send_command` raises. Setup takes the
configuration from the file's `device` block, and the reads answer from it; a board it leaves
`None` does not answer, and the device name is not simulated. Each feature has a simulation
subclass that overrides its `_unchecked_fw_*` calls and reads with state above the wire:
`MantisSimulationPlateClamp` reads 0 until a command sets clamping done or unclamping done;
`MantisSimulationRfid` reads every slot empty.

**M16. Locking is by subsystem.** (STAR P11.) One lock serializes the link today. When features
add commands that the device runs together, each subsystem gets its own lock, taken in declaration
order.

**M17. The master holds every link to the device.** (STAR P21.) The FTDI has two interfaces; the
master opens both in `setup` and closes them in `stop`, and a feature sends only through the
master's links. Interface B carries the command frames; interface A, the RFID reader's commands.

**M18. The resource model mirrors the device; it never decides for it.** (STAR P12.) `MantisDevice`
builds the parts and holds the driver; a feature reaches the parts through the device, and moves
keep them in step. A driver with no resource tree still drives.

## Open, and where this is not consistent yet

1. **`CommandRefusedError` is untested.** No reply seen has a non-zero status.
2. **The v3 board set is an inference.** No v3 has run discovery, so whether a v3 lacks board 11,
   or answers board 10 or 12, is unproven.
3. **Three link values are ours, not the vendor's.** The latency timer (2 ms), the read poll
   (10 ms) and the reply timeout (5 s). A frame whose reserved word is not zero is logged, not
   refused.
4. **The arm and Z column are not modelled yet.** They come with the arm feature, placed from a
   pose read from the device; which motor drives which link is not measured.
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
