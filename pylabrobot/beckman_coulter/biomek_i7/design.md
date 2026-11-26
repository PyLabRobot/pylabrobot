# How the Biomek i7 integration is built

The patterns this driver follows. It follows the STAR's (`pylabrobot/hamilton/star/design.md`,
P1-P20); this says only where the Biomek differs or adds something.

## What makes the Biomek different

A STAR takes operations ("pick up tips here") and plans the motion itself. A Biomek's boards take
one axis at a time, in encoder counts. Three layers of Beckman software sit between a method and the
boards: **Biomek** (the application) plans operations, safe heights, collisions and liquid handling;
**the Master Controller** (MC) takes host commands in counts (`AM`, `HM`, `LT`...), coordinates each
pod's axes and keeps their positions; **the ACE boards** (a chassis and one per pod) take
single-axis commands over USB. This driver replaces the top two and talks to the boards directly.

| Layer | What it reproduces | Where |
|---|---|---|
| L1 wire | the ACE messages | `driver/framing.py`, `board.py`, `protocol.py` |
| L2 board commands | one board request each | `_unchecked_fw_*` on the pods and chassis |
| L3 MC | one MC host command each | the pods' public primitives, `driver/recipes/` |
| L4 planning | Biomek's planning | `driver/motion.py`, `pod_geometry.py`, `liquid.py` (no I/O) |
| L5 PyLabRobot | the PLR API | `device.py`, the operation features, `resource_model/` |

Each layer calls only the one below it.

**I1. The boards say what is fitted.** `discover()` (read-only) asks each board its module type and
side; that places it, not the USB serial number or the enumeration order. An unknown type or side,
two boards claiming one place, or no chassis is refused. `driver/simulator.py` simulates the boards
at the wire, answering identity reads from a recording of a real instrument's.

**I2. The configuration is declared.** What the boards cannot report (motor tables, each axis's home
position and hysteresis, per pod the counts conversions, limits, geometry, calibration and settings)
is a `DeviceConfiguration`, read only from `declared_configuration_json` and written by
`save_configuration`. It describes what is fitted; it never decides what is present.

**I3. Units: a Biomek-unit core behind one boundary.** The public API is in PyLabRobot's units (mm,
uL, s, degrees, the deck's frame). Planning, recipes and the counts layer work in Biomek's (cm, its
frame with Y towards the front, the per-mille speed field), which is what its logs and files are
compared in. A public method converts its arguments once, in `units.py`, and calls its `_`-prefixed
Biomek-unit twin; nothing is converted back and forth inside.

**I4. One way to make a configuration, checked against the instrument.**
`DeviceConfiguration.from_biomek` (`biomek_files/`) makes it from the instrument's Biomek instrument
file (`.bif`) and the Master Controller's instrument record, read-only; the two must describe one
instrument. Each source's values are fingerprinted: a file changed after import is refused at load;
on the instrument, discovery refuses one whose serial numbers are not the modules', and one whose
sources have changed since. There is no switch to skip a check; the fix is to import again. A
simulation with no files uses the package's generic instrument (`defaults/`), which a live session
refuses.

## The pods

`left_x_arm` / `right_x_arm` (STAR's names) are `Pod`s, one ACE board each, on one shared X rail. A
pod's axes, head or channels and gripper are driven by one board through one position record, so the
pod keeps the shared mechanics (moves, homing, enabling, the reads, every `_unchecked_fw_*`) and its
features send through it. `driver.arm(name)` also takes Biomek's names (`1`, `"Pod1"`, `"MC_1"`).

**I5. Safety state.** The instrument rests with the interlock tripped; the driver arms it only to
move and leaves it as it found it. `stop()` moves nothing (Biomek stops in place). `pause` /
`resume` is Biomek's whole-system pause. A fault under power is cleared by enabling, with a warning;
one that will not clear trips the interlock. The door only reports: it has no interlock of its own.

**I6. Positions are kept as the MC keeps them.** Each axis has `(position, adjusted)`; moves are
planned from where the driver last put the axis, not from a read. An axis whose position is unknown
(after homing, a force-limited move or a failure) is read before its next move. That, and sending
each move's requests in the MC's order, keeps the traffic request for request the MC's. Home All is
the MC's recipe (`driver/recipes/homing.py`), its targets from the motor record.

**I7. Pod against pod is the driver's job.** Nothing below the driver stops the pods colliding.
Before a pod moves, its swept X range (bridge, pipettor and gripper as posed, with what it holds)
must clear the other pod's range and any move it has in flight, or the move is refused before
anything is sent (`driver/x_rail.py`, Biomek's own rule). The driver does not move the other pod out
of the way; a caller makes room (`Pod.move_aside`, `Pod.park`). The guard uses the configuration
only, so it holds without a deck.

## The deck and the device

**I8. The deck is optional; the device holds the models.** The driver homes, parks and moves with no
deck. `BiomekI7Device` (built by `BiomekI7(...)`, or `simulation=True`) is the resource tree: the
deck, and per pod a bridge and pod model built from the declared geometry, following the pod's axes.
The features hold their heads as the STAR's do (`head96.resource`, `pipettes.resources`).

**I9. Targeting is PyLabRobot-native, with one Biomek correction** (`targeting.py`): a target is the
resource's location on the deck plus the offset, in Biomek's frame, plus the moving pod's
calibration of the deck position it stands on. Positions centre their labware, as Biomek does.

**I10. Labware is Biomek's own.** Biomek's labware and tip classes are PyLabRobot definitions
(`pylabrobot.resources.beckman_coulter`); `resource_model/labware.py` names them by class, and
`layouts.place_labware` places a method's Instrument Setup. A tip's end is its length less its
seating depth; a tip-loading position's adaptor plate takes its labware 0.254 cm lower.

**I11. Tips are shaft state.** A tip a head carries is a child of its `TipMountingShaft`, whose
state is the tip (`serialize_state`). The device saves the tree's state (`state_json`) whenever a
head's tips change, restores it when built, and carries it over to the models it rebuilds at setup:
the 96-head cannot sense tips, so that is how they are known across sessions.

**I12. Travel is a traverse height, not a path planner.** Before X/Y travel the channels and the
gripper rise to a traverse height (by default near the top of travel; per call, as STAR's
`minimum_traverse_height_*`). A height below what the deck model and Biomek's per-pod obstacles need
is refused, not routed.

## Operation features

A feature exists once discovery says it is fitted (STAR's P2): `pipettes` on a Span-8 pod. Shortcuts
(`driver.pipettes`, `lh.pipettes`) give it when exactly one pod carries it. Calls take STAR's shape:
the arguments every PyLabRobot liquid handler shares are positional, in STAR's order; the rest are
keyword-only, so a STAR positional argument this driver lacks is refused, not misread. Where the
Span-8 applies one value to all probes, a per-channel list whose values differ is refused.

**I13. Pipetting follows Biomek's templates** (`driver/liquid.py`): its height rule (from the
bottom, top or liquid), following the liquid, mixes, conditioning, blowout, trailing air and the
touch-off, for any PyLabRobot container from its height <-> volume function. A setting the driver
does not build yet is refused, never run without it. The Span-8's tips are checked by its tip
sensor; it cannot drop tips into a rack, only discard them.

`head96` is the Multichannel pod's. Its tips are loaded force-limited by the MC's `LT`
(`driver/recipes/tip_load.py`, its arguments from the configuration) and ejected by the plunger's
shuck. It has no tip sensor, so `setup` ejects over its trash even with no tips recorded
(`precautionary_discard`).

**I14. A grip is Biomek's.** `<pod>.gripper` takes STAR's iSWAP / CoRe gripper calls with a
`direction`: travel turned, open, down, a force-limited close that stops on the labware, lift clear,
carry, place. The grip point is the resource's `preferred_pickup_location`; the widths default to
Biomek's margins around the labware's width; a close that reaches its target met nothing and is
refused. A pod's own gripper and pipettor are kept apart (`driver/pod_collision.py`).

**I15. Biomek's techniques are a translation, not a mode.** `techniques.py` turns a Biomek technique
and liquid type into the features' keyword arguments, which a caller splats and overrides. There is
no `technique=` argument and no selection by rank.
