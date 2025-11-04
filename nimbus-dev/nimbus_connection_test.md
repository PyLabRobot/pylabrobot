# Hamilton Nimbus TCP Connection Guide

## Overview

This document describes the Hamilton TCP protocol implementation and the connection flow used to successfully communicate with the Nimbus instrument. The implementation uses a clean, layered architecture that mirrors the protocol specification.

## Architecture

### Layered Protocol Stack

The Hamilton TCP protocol uses a nested packet structure with distinct layers, each with specific responsibilities:

```
┌─────────────────────────────────────────────────────────────────┐
│                    APPLICATION LAYER                            │
│  Commands (commands.py) - High-level API for instrument ops     │
│  - HamiltonCommand base class                                   │
│  - Introspection commands (tcp_introspection.py)                │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MESSAGE BUILDERS (messages.py)               │
│  - InitMessage: IP[Connection] - Protocol 7                     │
│  - RegistrationMessage: IP[HARP[Registration]] - Protocol 3     │
│  - CommandMessage: IP[HARP[HOI]] - Protocol 2                   │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PACKET LAYER (packets.py)                    │
│  - IpPacket: Transport layer (size, protocol, version)          │
│  - HarpPacket: Protocol layer (addressing, sequencing)          │
│  - HoiPacket: Application payload (method calls)                │
│  - RegistrationPacket: Discovery payload                        │
│  - ConnectionPacket: Initialization payload                     │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PARAMETER ENCODING (hoi_params.py)           │
│  - HoiParams: Build DataFragment-wrapped parameters             │
│  - HoiParamsParser: Parse DataFragment-wrapped responses        │
│  - Automatic DataFragment wrapping for HOI protocol             │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SERIALIZATION (wire.py)                      │
│  - Wire.write(): Build primitives (u8, u16, i32, string, etc.)  │
│  - Wire.read(): Parse primitives                                │
│  - Low-level byte packing/unpacking                             │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CONSTANTS (protocol.py)                      │
│  - HamiltonProtocol enum (protocol identifiers)                 │
│  - Hoi2Action enum (action codes)                               │
│  - RegistrationActionCode enum                                  │
│  - Version constants                                            │
└─────────────────────────────────────────────────────────────────┘
```

## Protocol Packet Structure

### Packet Nesting

Hamilton uses nested packet structures that match the architectural layers:

```
┌─ IP PACKET ──────────────────────────────────────────────────┐
│ [size:2][protocol:1][version:1][options_len:2][options][...] │
│                                                              │
│  ┌─ HARP PACKET (if IP protocol = 6) ─────────────────────┐  │
│  │ [src:6][dst:6][seq:1][reserved:1][protocol:1]          │  │
│  │ [action:1][msg_len:2][opts_len:2][options]             │  │
│  │ [version:1][reserved:1][...]                           │  │
│  │                                                        │  │
│  │  ┌─ HOI PACKET (if HARP protocol = 2) ──────────────┐  │  │
│  │  │ [interface_id:1][action:1][action_id:2]          │  │  │
│  │  │ [version:1][num_fragments:1]                     │  │  │
│  │  │                                                  │  │  │
│  │  │  ┌─ DataFragment (repeated) ─────────────────┐   │  │  │
│  │  │  │ [format:1][flags:1][length:2][data:n]     │   │  │  │
│  │  │  └───────────────────────────────────────────┘   │  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  │                                                        │  │
│  │  OR                                                    │  │
│  │                                                        │  │
│  │  ┌─ REGISTRATION PACKET (if HARP protocol = 3) ────┐   │  │
│  │  │ [action_code:2][response_code:2][version:1]     │   │  │
│  │  │ [reserved:1][req_addr:6][res_addr:6]            │   │  │
│  │  │ [options_len:2]                                 │   │  │
│  │  │                                                 │   │  │
│  │  │  ┌─ Registration Option (repeated) ─────────┐   │   │  │
│  │  │  │ [option_type:1][length:1][data:n]        │   │   │  │
│  │  │  │ (e.g. HARP_PROTOCOL_REQUEST:             │   │   │  │
│  │  │  │  [protocol:1][request_id:1])             │   │   │  │
│  │  │  └──────────────────────────────────────────┘   │   │  │
│  │  └─────────────────────────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  OR (if IP protocol = 7)                                     │
│                                                              │
│  ┌─ CONNECTION PACKET ────────────────────────────────────┐  │
│  │ [version:1][msg_id:1][count:1][unknown:1]              │  │
│  │ [raw parameters - NOT DataFragments]                   │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### Key Protocol Values

**IP Protocol Field:**
- `2` = PIPETTE - Direct pipette operations
- `6` = OBJECT_DISCOVERY - Most common (wraps HARP)
- `7` = INITIALIZATION - Connection setup

**HARP Protocol Field (when IP = 6):**
- `2` = HOI2 - Method calls with DataFragments
- `3` = REGISTRATION2 - Object discovery

**Action Byte Format:**
```
action_byte = action_code | (0x10 if response_required else 0x00)

Examples:
  COMMAND_REQUEST with response:  3 | 0x10 = 0x13
  COMMAND_RESPONSE:                4 | 0x00 = 0x04
```

**Version Bytes (Critical!):**
- IP packet version: `0x30` (major=3, minor=0)
- HARP packet version: `0x00` (NOT 0x30!)
- Registration packet version: `0x00` (NOT 0x30!)

### DataFragment Types

HOI parameters use DataFragment wrapping:

| Type ID | Type    | Wire Size | Example Value |
|---------|---------|-----------|---------------|
| 6       | i8      | 1 byte    | -128 to 127   |
| 7       | u8      | 1 byte    | 0 to 255      |
| 8       | i16     | 2 bytes   | -32768 to 32767 |
| 9       | u16     | 2 bytes   | 0 to 65535    |
| 10      | i32     | 4 bytes   | ±2 billion    |
| 11      | u32     | 4 bytes   | 0 to 4 billion |
| 16      | bool    | 1 byte    | 0 or 1        |
| 19      | string  | variable  | UTF-8, null-terminated |
| 26+     | arrays  | variable  | type_id + 20  |

**Exception:** Protocol 7 (INITIALIZATION) uses raw parameters, NOT DataFragments.

## Connection Flow

1. **TCP Connect** → Establish socket to `192.168.100.100:2000`
2. **Initialize** → `InitMessage()` (Protocol 7) → Get client_id (e.g., `100:0:0`)
3. **Discover** → `RegistrationMessage()` to `0:0:65534` → Get root object IDs
4. **Introspect** → `CommandMessage()` to objects → Query methods/properties

## Key Implementation Notes

**Addressing:** `module:node:object` format
- `0:0:65534` = Registration service
- `client_id:0:0` = Your client address
- Sequence numbers tracked per destination

**DataFragment Wrapping:**
```python
# Packet structure uses raw Wire
Wire.write().i32(100).finish()

# HOI parameters use HoiParams (automatic DataFragment wrapping)
HoiParams().i32(100).build()
```

## Usage Examples

### Basic Connection
```python
backend = TCPBackend(host="192.168.100.100", port=2000)
await backend.setup()
# backend.client_address is now set, root objects discovered
await backend.stop()
```

### Send a Command
```python
msg = CommandMessage(dest=Address(0, 0, 1), interface_id=0, method_id=42)
msg.add_i32(100).add_string("test")
packet = msg.build(src=backend.client_address, seq=1)

await backend.write(packet)
response = CommandResponse.from_bytes(await backend._read_one_message())
result = HoiParamsParser(response.hoi_params).i32()
```

### Discover Root Objects
```python
msg = RegistrationMessage(dest=Address(0, 0, 65534), action_code=12)
msg.add_registration_option(
    option_type=RegistrationOptionType.HARP_PROTOCOL_REQUEST,
    protocol=2, request_id=1
)
packet = msg.build(src=backend.client_address, req_addr=Address(0,0,0),
                   res_addr=Address(0,0,0), seq=1, harp_action=0x13)
```

