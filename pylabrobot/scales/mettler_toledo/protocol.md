# Protocol: MT-SICS (Mettler Toledo Standard Interface Command Set)

<!--
  Template version: 1.0
  This document follows the PyLabRobot device protocol documentation pattern.
  Copy this structure when documenting a new device backend's communication protocol.
-->

## Overview

| Property | Value |
|----------|-------|
| Protocol name | MT-SICS (Mettler Toledo Standard Interface Command Set) |
| Transport | Serial (RS-232) via USB-to-serial adapter |
| Encoding | ASCII text |
| Baud rate | 9600 |
| Line terminator | CR LF (`\r\n`, 0x0D 0x0A) |
| Direction | Half-duplex (send command, wait for response) |
| Spec document | [MT-SICS Reference Manual](https://web.archive.org/web/20240208213802/https://www.mt.com/dam/product_organizations/industry/apw/generic/11781363_N_MAN_RM_MT-SICS_APW_en.pdf) |

## Command format (PLR to device)

```
<CMD> [<param1> <param2> ...] CR LF
```

- Commands are uppercase ASCII
- Parameters separated by spaces
- Quoted strings use `"text"`
- Each command must be followed by CR LF

Examples:
```
S\r\n              -- read stable weight
ZI\r\n             -- zero immediately
M21 0 0\r\n        -- set host unit to grams
D "Hello"\r\n      -- write text to display
```

## Response format (device to PLR)

### Standard response (single line)

```
<CMD> <Status> [<data> ...] [<unit>] CR LF
```

The response echoes the command name, followed by a status character, optional data fields, and an optional unit.

### Status codes

| Status | Meaning |
|--------|---------|
| `A` | Command executed successfully (final response) |
| `B` | Command not yet terminated, additional responses follow |
| `S` | Stable weight value |
| `D` | Dynamic (unstable) weight value |
| `I` | Command understood but not executable (device busy) |
| `L` | Logical error (parameter not allowed) |
| `+` | Overload (weighing range exceeded) |
| `-` | Underload (weighing pan not in place) |

### Error responses (no status field)

```
ES CR LF           -- syntax error (command not recognized)
ET CR LF           -- transmission error (parity/break)
EL CR LF           -- logical error (command cannot execute)
```

These are 2-character responses with no status field or data.

### Weight response errors

```
S S    Error <code><trigger> CR LF
```

The weight value field is replaced with an error code when the device detects a hardware fault. See spec Section 2.1.3.3.

## Multi-response commands

Commands that return status `B` send multiple lines. The final line has status `A`.

Example - I50 (remaining weighing ranges):
```
PLR sends:    I50\r\n
Device sends: I50 B 0  535.141 g\r\n    -- RangeNo 0, more lines follow
              I50 B 1  -18.973 g\r\n    -- RangeNo 1, more lines follow
              I50 A 2  335.465 g\r\n    -- RangeNo 2, final response
```

Example - C (cancel all):
```
PLR sends:    C\r\n
Device sends: C B\r\n                   -- cancel started
              C A\r\n                   -- cancel complete
```

`send_command()` reads all lines until it sees status `A` (or non-`B`).

## Exceptions to the standard format

### @ (cancel) response echoes I4, not @

```
PLR sends:    @\r\n
Device sends: I4 A "B207696838"\r\n
```

The @ command resets the device and responds with the serial number using the I4 response format, not the @ command name.

### Commands that always fail on WXS205SDU

`ZC` (zero with timeout) and `TC` (tare with timeout) return `ES` (syntax error) on the WXS205SDU despite being listed in the MT-SICS spec. These commands may work on other MT-SICS devices.

## Command levels

MT-SICS commands are grouped into levels. The device reports which levels it supports via the I1 command.

| Level | Description | Availability |
|-------|-------------|-------------|
| 0 | Basic set: identification, weighing, zero, tare, cancel | Always available |
| 1 | Elementary: display, tare memory, timed commands | Always available |
| 2 | Extended: configuration, device info, diagnostics | Model-dependent |
| 3 | Application-specific: filling, dosing, calibration | Model-dependent |

See `mt_sics_commands.md` for the full command reference with implementation status.
