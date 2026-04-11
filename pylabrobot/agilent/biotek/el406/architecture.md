# BioTek EL406 — Architecture

## Overview

The EL406 plate washer has four subsystems:

1. **Manifold** — aspirate, dispense, wash cycles, prime, auto-clean (vacuum-based)
2. **Syringe pumps** — precise dispense/prime via dual syringes (A/B)
3. **Peristaltic pumps** — continuous-flow dispense/prime/purge via cassettes
4. **Shaker** — plate shaking with soak periods

Each subsystem maps to a separate capability in the new architecture.

## Current migration status

| Subsystem | Capability | Backend | Status |
|-----------|-----------|---------|--------|
| Manifold | `PlateWasher96` | `EL406PlateWasher96Backend` | Done |
| Syringe | TBD | TBD | Not started |
| Peristaltic | TBD (`PumpingCapability`?) | TBD | Not started |
| Shaker | `ShakingCapability` (existing) | TBD | Not started |

## Class diagram

```
EL406 (Device, Resource)
  ├── _driver: EL406Driver
  │     ├── FTDI I/O (setup/stop, serial config)
  │     ├── Command sending (send_framed_command, send_action_command, send_step_command)
  │     ├── Polling (poll_device_state, wait_until_ready)
  │     ├── Batch management (batch context manager, start_batch)
  │     └── Device-level ops (reset, home_motors, pause, resume, abort, set_washer_manifold)
  │
  ├── washer: PlateWasher96
  │     └── backend: EL406PlateWasher96Backend
  │           ├── PlateWasher96Backend interface: aspirate, dispense, wash, prime
  │           ├── Full manifold API: aspirate, dispense,
  │           │   wash, prime, auto_clean
  │           └── Command builders (_build_aspirate_command, _build_wash_composite_command, etc.)
  │
  └── plate_holder: PlateHolder
```

## File layout

```
pylabrobot/agilent/biotek/el406/
├── __init__.py                  # Exports EL406, EL406Driver, EL406PlateWasher96Backend
├── driver.py                    # EL406Driver — FTDI I/O, lifecycle, device-level ops
├── plate_washing_backend.py     # EL406PlateWasher96Backend — manifold protocol encoding
├── el406.py                     # EL406 Device — wires driver + capabilities
└── architecture.md              # This file
```

## Shared modules (in legacy, to be moved later)

The driver and backend import utility modules that still live under the legacy path:

- `legacy/.../protocol.py` — `build_framed_message()` wire framing
- `legacy/.../helpers.py` — `plate_to_wire_byte()`, plate defaults
- `legacy/.../enums.py` — `EL406WasherManifold`, `EL406Motor`, etc.
- `legacy/.../errors.py` — `EL406CommunicationError`, `EL406DeviceError`
- `legacy/.../error_codes.py` — error code lookup table

These are protocol/hardware constants, not legacy API. They should eventually move to
`pylabrobot/agilent/biotek/el406/` once all subsystems are migrated.

## Wire protocol

- **Transport**: FTDI USB, 38400 baud, 8N2, no flow control
- **Framing**: 11-byte header (start marker, version, command LE16, constant, reserved, data length LE16, checksum LE16) + data
- **Flow**: Command → ACK (0x06) → response header + data. Step commands require STATUS_POLL (0x92) polling for completion.

### Manifold command codes

| Command | Code | Payload size |
|---------|------|-------------|
| Aspirate | 0xA5 | 22 bytes |
| Dispense | 0xA6 | 20 bytes |
| Wash | 0xA4 | 102 bytes |
| Prime | 0xA7 | 13 bytes |
| Auto-clean | 0xA8 | 8 bytes |

## Usage

```python
from pylabrobot.agilent.biotek.el406 import EL406
from pylabrobot.resources import Plate

el406 = EL406(name="washer")
await el406.setup()

plate = Plate(...)  # your plate resource

# Simple API (via PlateWasher96)
await el406.washer.wash(plate, cycles=3, dispense_volume=300)
await el406.washer.aspirate(plate)
await el406.washer.dispense(plate, volume=200)

# Full EL406 manifold API (via backend)
await el406.washer.backend.wash(
    plate, cycles=5, buffer="B", dispense_flow_rate=9,
    shake_duration=30, shake_intensity="Medium",
)
await el406.washer.backend.prime(plate, volume=10000, buffer="A")
await el406.washer.backend.auto_clean(plate, buffer="A", duration=120)

# Device-level ops (via driver)
await el406._driver.reset()
await el406._driver.set_washer_manifold(EL406WasherManifold.TUBE_96_DUAL)

await el406.stop()
```
