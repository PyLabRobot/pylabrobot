---
orphan: true
---

# Reverse-engineering the BD FACSMelody

## Why this is worth doing

Sorting is the one step in plate-based single-cell sequencing that usually has to
happen off-automation. A liquid handler can build libraries end to end, but the
sort itself, one gated cell per well or a targeted, enriched population, is
typically a manual hand-off. Bring the sorter under PyLabRobot and that gap closes:
the deck stages the plate, calls `sort_to_plate`, and finishes the prep. Walkaway
library prep becomes possible.

The same control also unlocks sorting for a target population. Enrich a cell type,
a marker-positive subset, or live singlets before the assay, and every downstream
measurement inherits that resolution. For cell-type-specific epigenomics this is
the whole point: you want to read regulatory state in the exact cell type a
variant acts in, not in a bulk average that blurs it away.

The BD FACSMelody is a closed instrument driven by BD FACSChorus, so getting there
means reverse-engineering its control link. This document is the playbook.

## The method (credit where it is due)

This follows the methodology **Rick Wierenga** used to build PyLabRobot's device
backends: do not invent a protocol, work down to the OEM's own command layer,
sniff the OEM-to-device traffic, correlate each UI action to its bytes, decode the
framing, and replay over PyUSB or pyserial before wrapping it in a backend. The
same approach produced PLR's Hamilton STAR and Tecan EVO backends from captured
traffic and firmware command strings.

The steps below are numbered so the path is reproducible and so a reviewer can see
exactly where the safety checks sit: the backend refuses to drive hardware until
every required command is decoded, and it refuses to actuate without a human in
the loop.

The reverse-engineering toolkit that produces the deliverable lives in a separate
repository ([plr-clarity](https://github.com/di-omics/plr-clarity)) and is not
vendored into PyLabRobot. Only the consumer side ships here: the backend loads the
toolkit's output, a `ProtocolMap`, and turns it into `CellSorter` operations.

## The deliverable: a ProtocolMap

A `ProtocolMap` is a JSON file mapping each logical command (`start_sort`,
`clean`, and so on) to the exact bytes that drive it, plus how each frame is framed
and checksummed. The minimum set a sort-to-plate run needs is fixed:

`connect`, `get_status`, `load_template`, `set_deposition`, `prime`, `start_sort`,
`wait_complete`, `abort`, `clean`.

Combinatorial indexing tolerates tens to a hundred cells per well and does not need
index-sorting (recording which cell landed where). So the target is small: trigger
a saved gate template, configure count-controlled deposition, and poll status. We
mostly reverse-engineer how to trigger a saved template and move plates, not BD's
gating math.

## Step 1: map the OEM stack and transport

FACSChorus talks to the Melody over USB and/or an Ethernet cart link. Enumerate
everything, unplug and replug the instrument, and diff the enumeration to isolate
its link. Record the endpoint (for example `usb:0x1fbd:0x0002`, `COM4`, or
`10.0.0.5:9100`) and which transport it uses.

The highest-leverage move is often not the wire but FACSChorus itself: its logs,
a local control daemon, and the experiment database. If Chorus logs the command
bytes or exposes a localhost service, you may not need to sniff USB at all.

## Step 2: capture traffic while performing labeled UI actions

Start your platform sniffer on the Melody's interface, then drive Chorus by hand.

- Windows: Wireshark with USBPcap on the USB interface, exported to `.pcapng`.
- Linux: `usbmon` or Wireshark.
- Serial: a COM sniffer saved as `<timestamp> <direction> <hexbytes>` lines.

The core trick: perform one discrete Chorus action, mark the instant, and look
only at the bytes inside that window. Cover every required command (click "Start
Sort" and mark `start_sort`, click "Abort" and mark `abort`, and so on).

## Step 3: correlate action to bytes, decode the framing

For each labeled window, isolate the frames unique to that action by diffing across
windows to cancel the periodic keep-alive and status chatter. Then work out the
structure:

- framing: fixed length, terminator bytes, or length-prefixed.
- opcode: the longest common prefix across a command's frames.
- parameters: the bytes that change when you vary one setting (cells per well,
  well count). Find their encoders by changing one thing in Chorus and diffing.
- checksum: brute-force the common schemes (sum8, xor8, CRC16 variants) over
  candidate byte ranges.

Each hypothesis carries its evidence (the capture frames it came from) so a human
confirms it before anything is replayed.

## Step 4: build the ProtocolMap with coverage tracking

Fold the decoded commands into a `ProtocolMap` seeded with the required set, so the
result reports exactly which required commands are decoded and which are still
missing. Coverage is the gate: the backend will not open a live link until it is
complete.

## Step 5: guarded replay

The Melody is a laser plus pressurized fluidics, so replay is conservative by
default, with two independent safety switches, both off by default:

- `armed` opens the link and permits transmission. Without it, everything is a
  dry-run that logs the exact bytes and sends nothing.
- Commands that move fluid, open a nozzle, or fire a sort additionally require an
  actuation opt-in (`allow_actuation`) and a human present.

Confirm the read-only command first. Only once `get_status` round-trips cleanly do
you touch actuating commands, with BD service or your safety officer in the loop.
The backend refuses actuating commands without the opt-in and refuses any live run
against an incomplete map, so a half-mapped protocol can never drive the hardware.

## Step 6: validate against the instrument

Run a real sort-to-plate and confirm the deposition. This is the step that moves
the backend's label from "not yet hardware-validated" to "hardware-validated". The
backend reports which state it is in and does not claim a sort it has not run.

## Physical bridge (still required)

Software control is not full autonomy. You still need to move a sample tube onto
the SIP and the plate on and off the deposition stage (a bench cobot or a shared
plate hotel), and to expose the sorter's clog and error status as an interlock. A
clogged nozzle silently ruins a plate, so surface it.

## Decision gate

Reverse-engineering a closed sorter is real work. If the Melody is not a fixed
constraint, an API-controllable single-cell dispenser (cellenONE, WOLF G2,
Namocell Hana) does the same job, doublet exclusion plus count-controlled plate
deposition, with a documented interface and none of this RE. The orchestrator does
not change: the sorter becomes a different backend behind the same `sort_to_plate`
call. Decide the go or no-go after Step 1, once you know how open Chorus really is.
