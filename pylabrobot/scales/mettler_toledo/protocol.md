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

### @ (reset) response echoes I4, not @

```
PLR sends:    @\r\n
Device sends: I4 A "B207696838"\r\n
```

The @ command resets the device to its power-on state and responds with the serial number using the I4 response format, not the @ command name.

### Commands not supported on WXS205SDU (bridge mode)

The following commands return `ES` (syntax error) on the WXS205SDU WXA-Bridge
because they are not in the device's I0 command list. They may work on other
MT-SICS devices or on the same model with a terminal attached.

- `C` (cancel all), `SC` (timed read), `ZC` (timed zero), `TC` (timed tare)
- `D`, `DW` (display commands - no terminal in bridge mode)
- `I50` (remaining weighing range)

### I2 response format

The I2 response packs type, capacity, and unit into a single quoted string:
```
I2 A "WXS205SDU WXA-Bridge 220.00900 g"
```
The device type can contain spaces. Parse from the right: unit is the last
token, capacity is second-to-last, type is everything before.
`shlex.split` is used to handle quoted strings correctly.

### I15 uptime is in minutes

I15 returns uptime in minutes since last start or restart, with +/- 5% accuracy.
Response: `I15 A <Minutes>`. Example: `I15 A 123014` = ~85 days.

## Command discovery

**I0 is the definitive source of command support**, not I1.

I1 reports which standardized level sets are fully implemented. However, a device
can have individual commands from levels it does not fully support. The WXS205SDU
reports I1 levels [0, 1] but I0 discovers 62 commands across levels 0-3, including
M21, M28, and many other Level 2 commands.

During `setup()`, the backend queries I0 to discover all available commands.
Methods decorated with `@requires_mt_sics_command("CMD")` check against this list.

## Command levels

MT-SICS commands are grouped into levels. I1 reports level compliance but I0 is
the authoritative list of implemented commands.

| Level | Description | Availability |
|-------|-------------|-------------|
| 0 | Basic set: identification, weighing, zero, tare, reset (@) | Always available |
| 1 | Elementary: tare memory, timed commands, repeat | Always available |
| 2 | Extended: configuration, device info, diagnostics | Model-dependent |
| 3 | Application-specific: filling, dosing, calibration | Model-dependent |

## Date/time response format

DAT and TIM return space-separated fields, not a single string:
```
DAT A <Day> <Month> <Year>      -- e.g. DAT A 01 10 2021 = 1 Oct 2021
TIM A <Hour> <Minute> <Second>  -- e.g. TIM A 09 56 11 = 09:56:11
```

Both support set variants (`DAT DD MM YYYY`, `TIM HH MM SS`).
DAT set persists only via MT-SICS or FSET, not @.
TIM set also persists; only reset via MT-SICS, FSET, or terminal menu, not @.

## Write safety

Commands that modify device settings (M01 set, M02 set, M03 set, etc.) persist
to memory and survive power cycles. They cannot be undone with @ reset - only
via FSET (factory reset) or the terminal menu. Write methods are commented out
in the backend to prevent accidental modification.

Exceptions: `set_date()`, `set_time()`, and `set_device_id()` are active (not
commented out) since they do not change weighing behaviour.

## Interrupt safety

When a command is interrupted (KeyboardInterrupt or asyncio.CancelledError),
`send_command` sends `C` (cancel all) if the device supports it, otherwise just
flushes the serial buffer. Device state (zero, tare) is never cleared by an
interrupt. See the interrupt-safe command layer pattern.

See `mt_sics_commands.md` for the full command reference with implementation status.
