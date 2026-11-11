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
