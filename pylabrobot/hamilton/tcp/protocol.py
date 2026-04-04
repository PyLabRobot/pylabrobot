"""Hamilton TCP protocol constants and enumerations.

This module contains all protocol-level constants, enumerations, and type definitions
used throughout the Hamilton TCP communication stack.
"""

from __future__ import annotations

from enum import IntEnum

# Hamilton protocol version (from Piglet: version byte 0x30 = major 3, minor 0)
HAMILTON_PROTOCOL_VERSION_MAJOR = 3
HAMILTON_PROTOCOL_VERSION_MINOR = 0


class HamiltonProtocol(IntEnum):
  """Hamilton protocol identifiers.

  These values are derived from the piglet Rust implementation:
  - Protocol 2: PIPETTE - pipette-specific operations
  - Protocol 3: REGISTRATION - object registration and discovery
  - Protocol 6: OBJECT_DISCOVERY - general object discovery and method calls
  - Protocol 7: INITIALIZATION - connection initialization and client ID negotiation
  """

  PIPETTE = 0x02
  REGISTRATION = 0x03
  OBJECT_DISCOVERY = 0x06
  INITIALIZATION = 0x07


class Hoi2Action(IntEnum):
  """HOI2/HARP2 action codes (bits 0-3 of action field).

  Values from Hamilton.Components.TransportLayer.Protocols.HoiPacket2Constants.Hoi2Action

  The action byte combines the action code (lower 4 bits) with the response_required flag (bit 4):
  - action_byte = action_code | (0x10 if response_required else 0x00)
  - Example: COMMAND_REQUEST with response = 3 | 0x10 = 0x13
  - Example: STATUS_REQUEST without response = 0 | 0x00 = 0x00

  Common action codes:
  - COMMAND_REQUEST (3): Send a command to an object (most common for method calls)
  - STATUS_REQUEST (0): Request status information
  - COMMAND_RESPONSE (4): Response to a command
  - STATUS_RESPONSE (1): Response with status information

  NOTE: According to Hamilton documentation, both HARP2 and HOI2 use the same action
  enumeration values. This needs verification through TCP introspection.
  """

  STATUS_REQUEST = 0
  STATUS_RESPONSE = 1
  STATUS_EXCEPTION = 2
  COMMAND_REQUEST = 3
  COMMAND_RESPONSE = 4
  COMMAND_EXCEPTION = 5
  COMMAND_ACK = 6
  UPSTREAM_SYSTEM_EVENT = 7
  DOWNSTREAM_SYSTEM_EVENT = 8
  EVENT = 9
  INVALID_ACTION_RESPONSE = 10
  STATUS_WARNING = 11
  COMMAND_WARNING = 12


class HarpTransportableProtocol(IntEnum):
  """HARP2 protocol field values - determines payload type.

  From Hamilton.Components.TransportLayer.Protocols.HarpTransportableProtocol.
  The protocol field at byte 14 in HARP2 tells which payload parser to use.
  """

  HOI2 = 2  # Payload is Hoi2 structure (Protocol 2)
  REGISTRATION2 = 3  # Payload is Registration2 structure (Protocol 3)
  NOT_DEFINED = 0xFF  # Invalid/unknown protocol


class RegistrationActionCode(IntEnum):
  """Registration2 action codes (bytes 0-1 in Registration2 packet).

  From Hamilton.Components.TransportLayer.Protocols.RegistrationPacket2Constants.RegistrationActionCode2.

  Note: HARP action values for Registration packets are different from HOI action codes:
  - 0x13 (19): Request with response required (typical for HARP_PROTOCOL_REQUEST)
  - 0x14 (20): Response with data (typical for HARP_PROTOCOL_RESPONSE)
  - 0x03 (3): Request without response
  """

  REGISTRATION_REQUEST = 0  # Initial registration handshake
  REGISTRATION_RESPONSE = 1  # Response to registration
  DEREGISTRATION_REQUEST = 2  # Cleanup on disconnect
  DEREGISTRATION_RESPONSE = 3  # Deregistration acknowledgment
  NODE_RESET_INDICATION = 4  # Node will reset
  BRIDGE_REGISTRATION_REQUEST = 5  # Bridge registration
  START_NODE_IDENTIFICATION = 6  # Start identification
  START_NODE_IDENTIFICATION_RESPONSE = 7
  STOP_NODE_IDENTIFICATION = 8  # Stop identification
  STOP_NODE_IDENTIFICATION_RESPONSE = 9
  LIST_OF_REGISTERED_MODULES_REQUEST = 10  # Request registered modules
  LIST_OF_REGISTERED_MODULES_RESPONSE = 11
  HARP_PROTOCOL_REQUEST = 12  # Request objects (most important!)
  HARP_PROTOCOL_RESPONSE = 13  # Response with object list
  HARP_NODE_REMOVED_FROM_NETWORK = 14
  LIST_OF_REGISTERED_NODES_REQUEST = 15
  LIST_OF_REGISTERED_NODES_RESPONSE = 16


class RegistrationOptionType(IntEnum):
  """Registration2 option types (byte 0 of each option).

  From Hamilton.Components.TransportLayer.Protocols.RegistrationPacket2Constants.Option.

  These are semantic labels for the TYPE of information (what it means), while the
  actual data inside uses Hamilton type_ids (how it's encoded).
  """

  RESERVED = 0  # Padding for 16-bit alignment when odd number of unsupported options
  INCOMPATIBLE_VERSION = 1  # Version mismatch error (HARP version too high)
  UNSUPPORTED_OPTIONS = 2  # Unknown options error
  START_NODE_IDENTIFICATION = 3  # Identification timeout (seconds)
  HARP_NETWORK_ADDRESS = 4  # Registered module/node IDs
  HARP_PROTOCOL_REQUEST = 5  # Protocol request
  HARP_PROTOCOL_RESPONSE = 6  # PRIMARY: Contains object ID lists (most commonly used)


class HamiltonDataType(IntEnum):
  """Hamilton parameter data types for wire encoding in DataFragments.

  These constants represent the type identifiers used in Hamilton DataFragments
  for HOI2 command parameters. Each type ID corresponds to a specific data format
  and encoding scheme used on the wire.

  From Hamilton.Components.TransportLayer.Protocols.Parameter.ParameterTypes.
  """

  # Scalar integer types
  I8 = 1
  I16 = 2
  I32 = 3
  U8 = 4
  U16 = 5
  U32 = 6
  I64 = 36
  U64 = 37

  # Floating-point types
  F32 = 40
  F64 = 41

  # String and boolean
  STRING = 15
  BOOL = 23

  # Array types
  U8_ARRAY = 22
  I8_ARRAY = 24
  I16_ARRAY = 25
  U16_ARRAY = 26
  I32_ARRAY = 27
  U32_ARRAY = 28
  BOOL_ARRAY = 29
  STRING_ARRAY = 34
  I64_ARRAY = 38
  U64_ARRAY = 39
  F32_ARRAY = 42
  F64_ARRAY = 43


class HoiRequestId(IntEnum):
  """Request types for HarpProtocolRequest (byte 3 in command_data).

  From Hamilton.Components.TransportLayer.Protocols.RegistrationPacket2Constants.HarpProtocolRequest.HoiRequestId.
  """

  ROOT_OBJECT_OBJECT_ID = 1  # Request root objects (pipette, deck, etc.)
  GLOBAL_OBJECT_ADDRESS = 2  # Request global objects
  CPU_OBJECT_ADDRESS = 3  # Request CPU objects
