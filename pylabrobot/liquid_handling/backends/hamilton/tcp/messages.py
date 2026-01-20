"""High-level Hamilton message builders and response parsers.

This module provides user-facing message builders and their corresponding
response parsers. Each message type is paired with its response type:

Request Builders:
- InitMessage: Builds IP[Connection] for initialization
- RegistrationMessage: Builds IP[HARP[Registration]] for discovery
- CommandMessage: Builds IP[HARP[HOI]] for method calls

Response Parsers:
- InitResponse: Parses initialization responses
- RegistrationResponse: Parses registration responses
- CommandResponse: Parses command responses

This pairing creates symmetry and makes correlation explicit.

Architectural Note:
Parameter encoding (HoiParams/HoiParamsParser) is conceptually a separate layer
in the Hamilton protocol architecture (per documented architecture), but is
implemented here for efficiency since it's exclusively used by HOI messages.
This preserves the conceptual separation while optimizing implementation.

Example:
  # Build and send
  msg = CommandMessage(dest, interface_id=0, method_id=42)
  msg.add_i32(100)
  packet_bytes = msg.build(src, seq=1)

  # Parse response
  response = CommandResponse.from_bytes(received_bytes)
  params = response.hoi.params
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pylabrobot.io.binary import Reader, Writer
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import (
  Address,
  HarpPacket,
  HoiPacket,
  IpPacket,
  RegistrationPacket,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import (
  HamiltonDataType,
  HarpTransportableProtocol,
  RegistrationOptionType,
)

# ============================================================================
# HOI PARAMETER ENCODING - DataFragment wrapping for HOI protocol
# ============================================================================
#
# Note: This is conceptually a separate layer in the Hamilton protocol
# architecture, but implemented here for efficiency since it's exclusively
# used by HOI messages (CommandMessage).
# ============================================================================


class HoiParams:
  """Builder for HOI parameters with automatic DataFragment wrapping.

  Each parameter is wrapped with DataFragment header before being added:
  [type_id:1][flags:1][length:2][data:n]

  This ensures HOI parameters are always correctly formatted and eliminates
  the possibility of forgetting to add DataFragment headers.

  Example:
    Creates concatenated DataFragments:
    [0x03|0x00|0x04|0x00|100][0x0F|0x00|0x05|0x00|"test\0"][0x1C|0x00|...array...]

    params = (HoiParams()
              .i32(100)
              .string("test")
              .u32_array([1, 2, 3])
              .build())
  """

  def __init__(self):
    self._fragments: list[bytes] = []

  def _add_fragment(self, type_id: int, data: bytes, flags: int = 0) -> "HoiParams":
    """Add a DataFragment with the given type_id and data.

    Creates: [type_id:1][flags:1][length:2][data:n]

    Args:
      type_id: Data type ID
      data: Fragment data bytes
      flags: Fragment flags (default: 0, but BOOL_ARRAY uses 0x01)
    """
    fragment = Writer().u8(type_id).u8(flags).u16(len(data)).raw_bytes(data).finish()
    self._fragments.append(fragment)
    return self

  # Scalar integer types
  def i8(self, value: int) -> "HoiParams":
    """Add signed 8-bit integer parameter."""
    data = Writer().i8(value).finish()
    return self._add_fragment(HamiltonDataType.I8, data)

  def i16(self, value: int) -> "HoiParams":
    """Add signed 16-bit integer parameter."""
    data = Writer().i16(value).finish()
    return self._add_fragment(HamiltonDataType.I16, data)

  def i32(self, value: int) -> "HoiParams":
    """Add signed 32-bit integer parameter."""
    data = Writer().i32(value).finish()
    return self._add_fragment(HamiltonDataType.I32, data)

  def i64(self, value: int) -> "HoiParams":
    """Add signed 64-bit integer parameter."""
    data = Writer().i64(value).finish()
    return self._add_fragment(HamiltonDataType.I64, data)

  def u8(self, value: int) -> "HoiParams":
    """Add unsigned 8-bit integer parameter."""
    data = Writer().u8(value).finish()
    return self._add_fragment(HamiltonDataType.U8, data)

  def u16(self, value: int) -> "HoiParams":
    """Add unsigned 16-bit integer parameter."""
    data = Writer().u16(value).finish()
    return self._add_fragment(HamiltonDataType.U16, data)

  def u32(self, value: int) -> "HoiParams":
    """Add unsigned 32-bit integer parameter."""
    data = Writer().u32(value).finish()
    return self._add_fragment(HamiltonDataType.U32, data)

  def u64(self, value: int) -> "HoiParams":
    """Add unsigned 64-bit integer parameter."""
    data = Writer().u64(value).finish()
    return self._add_fragment(HamiltonDataType.U64, data)

  # Floating-point types
  def f32(self, value: float) -> "HoiParams":
    """Add 32-bit float parameter."""
    data = Writer().f32(value).finish()
    return self._add_fragment(HamiltonDataType.F32, data)

  def f64(self, value: float) -> "HoiParams":
    """Add 64-bit double parameter."""
    data = Writer().f64(value).finish()
    return self._add_fragment(HamiltonDataType.F64, data)

  # String and bool
  def string(self, value: str) -> "HoiParams":
    """Add null-terminated string parameter."""
    data = Writer().string(value).finish()
    return self._add_fragment(HamiltonDataType.STRING, data)

  def bool_value(self, value: bool) -> "HoiParams":
    """Add boolean parameter."""
    data = Writer().u8(1 if value else 0).finish()
    return self._add_fragment(HamiltonDataType.BOOL, data)

  # Array types
  def i8_array(self, values: list[int]) -> "HoiParams":
    """Add array of signed 8-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.i8(val)
    return self._add_fragment(HamiltonDataType.I8_ARRAY, writer.finish())

  def i16_array(self, values: list[int]) -> "HoiParams":
    """Add array of signed 16-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.i16(val)
    return self._add_fragment(HamiltonDataType.I16_ARRAY, writer.finish())

  def i32_array(self, values: list[int]) -> "HoiParams":
    """Add array of signed 32-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.i32(val)
    return self._add_fragment(HamiltonDataType.I32_ARRAY, writer.finish())

  def i64_array(self, values: list[int]) -> "HoiParams":
    """Add array of signed 64-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.i64(val)
    return self._add_fragment(HamiltonDataType.I64_ARRAY, writer.finish())

  def u8_array(self, values: list[int]) -> "HoiParams":
    """Add array of unsigned 8-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.u8(val)
    return self._add_fragment(HamiltonDataType.U8_ARRAY, writer.finish())

  def u16_array(self, values: list[int]) -> "HoiParams":
    """Add array of unsigned 16-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.u16(val)
    return self._add_fragment(HamiltonDataType.U16_ARRAY, writer.finish())

  def u32_array(self, values: list[int]) -> "HoiParams":
    """Add array of unsigned 32-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.u32(val)
    return self._add_fragment(HamiltonDataType.U32_ARRAY, writer.finish())

  def u64_array(self, values: list[int]) -> "HoiParams":
    """Add array of unsigned 64-bit integers.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.u64(val)
    return self._add_fragment(HamiltonDataType.U64_ARRAY, writer.finish())

  def f32_array(self, values: list[float]) -> "HoiParams":
    """Add array of 32-bit floats.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.f32(val)
    return self._add_fragment(HamiltonDataType.F32_ARRAY, writer.finish())

  def f64_array(self, values: list[float]) -> "HoiParams":
    """Add array of 64-bit doubles.

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)
    """
    writer = Writer()
    for val in values:
      writer.f64(val)
    return self._add_fragment(HamiltonDataType.F64_ARRAY, writer.finish())

  def bool_array(self, values: list[bool]) -> "HoiParams":
    """Add array of booleans (stored as u8: 0 or 1).

    Format: [element0][element1]... (NO count prefix - count derived from DataFragment length)

    Note: BOOL_ARRAY uses flags=0x01 in the DataFragment header (unlike other types which use 0x00).
    """
    writer = Writer()
    for val in values:
      writer.u8(1 if val else 0)
    return self._add_fragment(HamiltonDataType.BOOL_ARRAY, writer.finish(), flags=0x01)

  def string_array(self, values: list[str]) -> "HoiParams":
    """Add array of null-terminated strings.

    Format: [count:4][str0\0][str1\0]...
    """
    writer = Writer().u32(len(values))
    for val in values:
      writer.string(val)
    return self._add_fragment(HamiltonDataType.STRING_ARRAY, writer.finish())

  def build(self) -> bytes:
    """Return concatenated DataFragments."""
    return b"".join(self._fragments)

  def count(self) -> int:
    """Return number of fragments (parameters)."""
    return len(self._fragments)


class HoiParamsParser:
  """Parser for HOI DataFragment parameters.

  Parses DataFragment-wrapped values from HOI response payloads.
  """

  def __init__(self, data: bytes):
    self._data = data
    self._offset = 0

  def parse_next(self) -> tuple[int, Any]:
    """Parse the next DataFragment and return (type_id, value).

    Returns:
      Tuple of (type_id, parsed_value)

    Raises:
      ValueError: If data is malformed or insufficient
    """
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Insufficient data for DataFragment header at offset {self._offset}")

    # Parse DataFragment header
    reader = Reader(self._data[self._offset :])
    type_id = reader.u8()
    _flags = reader.u8()  # Read but unused
    length = reader.u16()

    data_start = self._offset + 4
    data_end = data_start + length

    if data_end > len(self._data):
      raise ValueError(
        f"DataFragment data extends beyond buffer: need {data_end}, have {len(self._data)}"
      )

    # Extract data payload
    fragment_data = self._data[data_start:data_end]
    value = self._parse_value(type_id, fragment_data)

    # Move offset past this fragment
    self._offset = data_end

    return (type_id, value)

  def _parse_value(self, type_id: int, data: bytes) -> Any:
    """Parse value based on type_id using dispatch table."""
    reader = Reader(data)

    # Dispatch table for scalar types
    scalar_parsers = {
      HamiltonDataType.I8: reader.i8,
      HamiltonDataType.I16: reader.i16,
      HamiltonDataType.I32: reader.i32,
      HamiltonDataType.I64: reader.i64,
      HamiltonDataType.U8: reader.u8,
      HamiltonDataType.U16: reader.u16,
      HamiltonDataType.U32: reader.u32,
      HamiltonDataType.U64: reader.u64,
      HamiltonDataType.F32: reader.f32,
      HamiltonDataType.F64: reader.f64,
      HamiltonDataType.STRING: reader.string,
    }

    # Check scalar types first
    # Cast int to HamiltonDataType enum for dict lookup
    try:
      data_type = HamiltonDataType(type_id)
      if data_type in scalar_parsers:
        return scalar_parsers[data_type]()
    except ValueError:
      pass  # Not a valid enum value, continue to other checks

    # Special case: bool
    if type_id == HamiltonDataType.BOOL:
      return reader.u8() == 1

    # Dispatch table for array element parsers
    array_element_parsers = {
      HamiltonDataType.I8_ARRAY: reader.i8,
      HamiltonDataType.I16_ARRAY: reader.i16,
      HamiltonDataType.I32_ARRAY: reader.i32,
      HamiltonDataType.I64_ARRAY: reader.i64,
      HamiltonDataType.U8_ARRAY: reader.u8,
      HamiltonDataType.U16_ARRAY: reader.u16,
      HamiltonDataType.U32_ARRAY: reader.u32,
      HamiltonDataType.U64_ARRAY: reader.u64,
      HamiltonDataType.F32_ARRAY: reader.f32,
      HamiltonDataType.F64_ARRAY: reader.f64,
      HamiltonDataType.STRING_ARRAY: reader.string,
    }

    # Handle arrays
    # Arrays don't have a count prefix - count is derived from DataFragment length
    # Calculate element size based on type
    element_sizes = {
      HamiltonDataType.I8_ARRAY: 1,
      HamiltonDataType.I16_ARRAY: 2,
      HamiltonDataType.I32_ARRAY: 4,
      HamiltonDataType.I64_ARRAY: 8,
      HamiltonDataType.U8_ARRAY: 1,
      HamiltonDataType.U16_ARRAY: 2,
      HamiltonDataType.U32_ARRAY: 4,
      HamiltonDataType.U64_ARRAY: 8,
      HamiltonDataType.F32_ARRAY: 4,
      HamiltonDataType.F64_ARRAY: 8,
      HamiltonDataType.STRING_ARRAY: None,  # Variable length, handled separately
    }

    # Cast int to HamiltonDataType enum for dict lookup
    try:
      data_type = HamiltonDataType(type_id)
      if data_type in array_element_parsers:
        element_size = element_sizes.get(data_type)
        if element_size is not None:
          # Fixed-size elements: calculate count from data length
          count = len(data) // element_size
          return [array_element_parsers[data_type]() for _ in range(count)]
        elif data_type == HamiltonDataType.STRING_ARRAY:
          # String arrays: null-terminated strings concatenated, no count prefix
          # Parse by splitting on null bytes
          strings = []
          current_string = bytearray()
          for byte in data:
            if byte == 0:
              if current_string:
                strings.append(current_string.decode("utf-8", errors="replace"))
                current_string = bytearray()
            else:
              current_string.append(byte)
          # Handle case where last string doesn't end with null (shouldn't happen, but be safe)
          if current_string:
            strings.append(current_string.decode("utf-8", errors="replace"))
          return strings
    except ValueError:
      # Not a valid enum value, continue to other checks
      # This shouldn't happen for valid Hamilton types, but we continue anyway
      pass

    # Special case: bool array (1 byte per element)
    if type_id == HamiltonDataType.BOOL_ARRAY:
      count = len(data) // 1  # Each bool is 1 byte
      return [reader.u8() == 1 for _ in range(count)]

    # Unknown type
    raise ValueError(f"Unknown or unsupported type_id: {type_id}")

  def has_remaining(self) -> bool:
    """Check if there are more DataFragments to parse."""
    return self._offset < len(self._data)

  def parse_all(self) -> list[tuple[int, Any]]:
    """Parse all remaining DataFragments.

    Returns:
      List of (type_id, value) tuples
    """
    results = []
    while self.has_remaining():
      results.append(self.parse_next())
    return results


# ============================================================================
# MESSAGE BUILDERS
# ============================================================================


class CommandMessage:
  """Build HOI command messages for method calls.

  Creates complete IP[HARP[HOI]] packets with proper protocols and actions.
  Parameters are automatically wrapped with DataFragment headers via HoiParams.

  Example:
      msg = CommandMessage(dest, interface_id=0, method_id=42)
      msg.add_i32(100).add_string("test")
      packet_bytes = msg.build(src, seq=1)
  """

  def __init__(
    self,
    dest: Address,
    interface_id: int,
    method_id: int,
    params: HoiParams,
    action_code: int = 3,  # Default: COMMAND_REQUEST
    harp_protocol: int = 2,  # Default: HOI2
    ip_protocol: int = 6,  # Default: OBJECT_DISCOVERY
  ):
    """Initialize command message.

    Args:
      dest: Destination object address
      interface_id: Interface ID (typically 0 for main interface, 1 for extended)
      method_id: Method/action ID to invoke
      action_code: HOI action code (default 3=COMMAND_REQUEST)
      harp_protocol: HARP protocol identifier (default 2=HOI2)
      ip_protocol: IP protocol identifier (default 6=OBJECT_DISCOVERY)
    """
    self.dest = dest
    self.interface_id = interface_id
    self.method_id = method_id
    self.params = params
    self.action_code = action_code
    self.harp_protocol = harp_protocol
    self.ip_protocol = ip_protocol

  def build(
    self,
    src: Address,
    seq: int,
    harp_response_required: bool = True,
    hoi_response_required: bool = False,
  ) -> bytes:
    """Build complete IP[HARP[HOI]] packet.

    Args:
      src: Source address (client address)
      seq: Sequence number for this request
      harp_response_required: Set bit 4 in HARP action byte (default True)
      hoi_response_required: Set bit 4 in HOI action byte (default False)

    Returns:
      Complete packet bytes ready to send over TCP
    """
    # Build HOI - it handles its own action byte construction
    hoi = HoiPacket(
      interface_id=self.interface_id,
      action_code=self.action_code,
      action_id=self.method_id,
      params=self.params.build(),
      response_required=hoi_response_required,
    )

    # Build HARP - it handles its own action byte construction
    harp = HarpPacket(
      src=src,
      dst=self.dest,
      seq=seq,
      protocol=self.harp_protocol,
      action_code=self.action_code,
      payload=hoi.pack(),
      response_required=harp_response_required,
    )

    # Wrap in IP packet
    ip = IpPacket(protocol=self.ip_protocol, payload=harp.pack())

    return ip.pack()


class RegistrationMessage:
  """Build Registration messages for object discovery.

  Creates complete IP[HARP[Registration]] packets for discovering modules,
  objects, and capabilities on the Hamilton instrument.

  Example:
    msg = RegistrationMessage(dest, action_code=12)
    msg.add_registration_option(RegistrationOptionType.HARP_PROTOCOL_REQUEST, protocol=2, request_id=1)
    packet_bytes = msg.build(src, req_addr, res_addr, seq=1)
  """

  def __init__(
    self,
    dest: Address,
    action_code: int,
    response_code: int = 0,  # Default: no error
    harp_protocol: int = 3,  # Default: Registration
    ip_protocol: int = 6,  # Default: OBJECT_DISCOVERY
  ):
    """Initialize registration message.

    Args:
      dest: Destination address (typically 0:0:65534 for registration service)
      action_code: Registration action code (e.g., 12=HARP_PROTOCOL_REQUEST)
      response_code: Response code (default 0=no error)
      harp_protocol: HARP protocol identifier (default 3=Registration)
      ip_protocol: IP protocol identifier (default 6=OBJECT_DISCOVERY)
    """
    self.dest = dest
    self.action_code = action_code
    self.response_code = response_code
    self.harp_protocol = harp_protocol
    self.ip_protocol = ip_protocol
    self.options = bytearray()

  def add_registration_option(
    self, option_type: RegistrationOptionType, protocol: int = 2, request_id: int = 1
  ) -> "RegistrationMessage":
    """Add a registration packet option.

    Args:
      option_type: Type of registration option (from RegistrationOptionType enum)
      protocol: For HARP_PROTOCOL_REQUEST: protocol type (2=HOI, default)
      request_id: For HARP_PROTOCOL_REQUEST: what to discover (1=root, 2=global)

    Returns:
      Self for method chaining
    """
    # Registration option format: [option_id:1][length:1][data...]
    # For HARP_PROTOCOL_REQUEST (option 5): data is [protocol:1][request_id:1]
    data = Writer().u8(protocol).u8(request_id).finish()
    option = Writer().u8(option_type).u8(len(data)).raw_bytes(data).finish()
    self.options.extend(option)
    return self

  def build(
    self,
    src: Address,
    req_addr: Address,
    res_addr: Address,
    seq: int,
    harp_action_code: int = 3,  # Default: COMMAND_REQUEST
    harp_response_required: bool = True,  # Default: request with response
  ) -> bytes:
    """Build complete IP[HARP[Registration]] packet.

    Args:
      src: Source address (client address)
      req_addr: Request address (for registration context)
      res_addr: Response address (for registration context)
      seq: Sequence number for this request
      harp_action_code: HARP action code (default 3=COMMAND_REQUEST)
      harp_response_required: Whether response required (default True)

    Returns:
      Complete packet bytes ready to send over TCP
    """
    # Build Registration packet
    reg = RegistrationPacket(
      action_code=self.action_code,
      response_code=self.response_code,
      req_address=req_addr,
      res_address=res_addr,
      options=bytes(self.options),
    )

    # Wrap in HARP packet
    harp = HarpPacket(
      src=src,
      dst=self.dest,
      seq=seq,
      protocol=self.harp_protocol,
      action_code=harp_action_code,
      payload=reg.pack(),
      response_required=harp_response_required,
    )

    # Wrap in IP packet
    ip = IpPacket(protocol=self.ip_protocol, payload=harp.pack())

    return ip.pack()


class InitMessage:
  """Build Connection initialization messages.

  Creates complete IP[Connection] packets for establishing a connection
  with the Hamilton instrument. Uses Protocol 7 (INITIALIZATION) which
  has a different structure than HARP-based messages.

  Example:
    msg = InitMessage(timeout=30)
    packet_bytes = msg.build()
  """

  def __init__(
    self,
    timeout: int = 30,
    connection_type: int = 1,  # Default: standard connection
    protocol_version: int = 0x30,  # Default: 3.0
    ip_protocol: int = 7,  # Default: INITIALIZATION
  ):
    """Initialize connection message.

    Args:
      timeout: Connection timeout in seconds (default 30)
      connection_type: Connection type (default 1=standard)
      protocol_version: Protocol version byte (default 0x30=3.0)
      ip_protocol: IP protocol identifier (default 7=INITIALIZATION)
    """
    self.timeout = timeout
    self.connection_type = connection_type
    self.protocol_version = protocol_version
    self.ip_protocol = ip_protocol

  def build(self) -> bytes:
    """Build complete IP[Connection] packet.

    Returns:
      Complete packet bytes ready to send over TCP
    """
    # Build raw connection parameters (NOT DataFragments)
    # Frame: [version:1][message_id:1][count:1][unknown:1]
    # Parameters: [id:1][type:1][reserved:2][value:2] repeated
    params = (
      Writer()
      # Frame
      .u8(0)  # version
      .u8(0)  # message_id
      .u8(3)  # count (3 parameters)
      .u8(0)  # unknown
      # Parameter 1: connection_id (request allocation)
      .u8(1)  # param id
      .u8(16)  # param type
      .u16(0)  # reserved
      .u16(0)  # value (0 = request allocation)
      # Parameter 2: connection_type
      .u8(2)  # param id
      .u8(16)  # param type
      .u16(0)  # reserved
      .u16(self.connection_type)  # value
      # Parameter 3: timeout
      .u8(4)  # param id
      .u8(16)  # param type
      .u16(0)  # reserved
      .u16(self.timeout)  # value
      .finish()
    )

    # Build IP packet
    packet_size = 1 + 1 + 2 + len(params)  # protocol + version + opts_len + params

    return (
      Writer()
      .u16(packet_size)
      .u8(self.ip_protocol)
      .u8(self.protocol_version)
      .u16(0)  # options_length
      .raw_bytes(params)
      .finish()
    )


# ============================================================================
# RESPONSE PARSERS - Paired with message builders above
# ============================================================================


@dataclass
class InitResponse:
  """Parsed initialization response.

  Pairs with InitMessage - parses Protocol 7 (INITIALIZATION) responses.
  """

  raw_bytes: bytes
  client_id: int
  connection_type: int
  timeout: int

  @classmethod
  def from_bytes(cls, data: bytes) -> "InitResponse":
    """Parse initialization response.

    Args:
      data: Raw bytes from TCP socket

    Returns:
      Parsed InitResponse with connection parameters
    """
    # Skip IP header (size + protocol + version + opts_len = 6 bytes)
    parser = Reader(data[6:])

    # Parse frame
    _version = parser.u8()  # Read but unused
    _message_id = parser.u8()  # Read but unused
    _count = parser.u8()  # Read but unused
    _unknown = parser.u8()  # Read but unused

    # Parse parameter 1 (client_id)
    _param1_id = parser.u8()  # Read but unused
    _param1_type = parser.u8()  # Read but unused
    _param1_reserved = parser.u16()  # Read but unused
    client_id = parser.u16()

    # Parse parameter 2 (connection_type)
    _param2_id = parser.u8()  # Read but unused
    _param2_type = parser.u8()  # Read but unused
    _param2_reserved = parser.u16()  # Read but unused
    connection_type = parser.u16()

    # Parse parameter 4 (timeout)
    _param4_id = parser.u8()  # Read but unused
    _param4_type = parser.u8()  # Read but unused
    _param4_reserved = parser.u16()  # Read but unused
    timeout = parser.u16()

    return cls(
      raw_bytes=data, client_id=client_id, connection_type=connection_type, timeout=timeout
    )


@dataclass
class RegistrationResponse:
  """Parsed registration response.

  Pairs with RegistrationMessage - parses IP[HARP[Registration]] responses.
  """

  raw_bytes: bytes
  ip: IpPacket
  harp: HarpPacket
  registration: RegistrationPacket

  @classmethod
  def from_bytes(cls, data: bytes) -> "RegistrationResponse":
    """Parse registration response.

    Args:
      data: Raw bytes from TCP socket

    Returns:
      Parsed RegistrationResponse with all layers
    """
    ip = IpPacket.unpack(data)
    harp = HarpPacket.unpack(ip.payload)
    registration = RegistrationPacket.unpack(harp.payload)

    return cls(raw_bytes=data, ip=ip, harp=harp, registration=registration)

  @property
  def sequence_number(self) -> int:
    """Get sequence number from HARP layer."""
    return self.harp.seq


@dataclass
class CommandResponse:
  """Parsed command response.

  Pairs with CommandMessage - parses IP[HARP[HOI]] responses.
  """

  raw_bytes: bytes
  ip: IpPacket
  harp: HarpPacket
  hoi: HoiPacket

  @classmethod
  def from_bytes(cls, data: bytes) -> "CommandResponse":
    """Parse command response.

    Args:
      data: Raw bytes from TCP socket

    Returns:
      Parsed CommandResponse with all layers

    Raises:
      ValueError: If response is not HOI protocol
    """
    ip = IpPacket.unpack(data)
    harp = HarpPacket.unpack(ip.payload)

    if harp.protocol != HarpTransportableProtocol.HOI2:
      raise ValueError(f"Expected HOI2 protocol, got {harp.protocol}")

    hoi = HoiPacket.unpack(harp.payload)

    return cls(raw_bytes=data, ip=ip, harp=harp, hoi=hoi)

  @property
  def sequence_number(self) -> int:
    """Get sequence number from HARP layer."""
    return self.harp.seq
