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
