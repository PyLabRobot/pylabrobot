"""Hamilton TCP packet structures.

This module defines the packet layer of the Hamilton protocol stack:
- IpPacket: Transport layer (size, protocol, version, payload)
- HarpPacket: Protocol layer (addressing, sequence, action, payload)
- HoiPacket: HOI application layer (interface_id, action_id, DataFragment params)
- RegistrationPacket: Registration protocol payload
- ConnectionPacket: Connection initialization payload

Each packet knows how to pack/unpack itself using the Wire serialization layer.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pylabrobot.io.binary import Reader, Writer

# Hamilton protocol version
HAMILTON_PROTOCOL_VERSION_MAJOR = 3
HAMILTON_PROTOCOL_VERSION_MINOR = 0


def encode_version_byte(major: int, minor: int) -> int:
  """Pack Hamilton version byte (two 4-bit fields packed into one byte).

  Args:
    major: Major version (0-15, stored in upper 4 bits)
    minor: Minor version (0-15, stored in lower 4 bits)
  """
  if not 0 <= major <= 15:
    raise ValueError(f"major version must be 0-15, got {major}")
  if not 0 <= minor <= 15:
    raise ValueError(f"minor version must be 0-15, got {minor}")
  version_byte = (minor & 0xF) | ((major & 0xF) << 4)
  return version_byte


def decode_version_byte(version_bite: int) -> tuple[int, int]:
  """Decode Hamilton version byte and return (major, minor).

  Returns:
    Tuple of (major_version, minor_version), each 0-15
  """
  minor = version_bite & 0xF
  major = (version_bite >> 4) & 0xF
  return (major, minor)


@dataclass(frozen=True)
class Address:
  """Hamilton network address (module_id, node_id, object_id)."""

  module: int  # u16
  node: int  # u16
  object: int  # u16

  def pack(self) -> bytes:
    """Serialize address to 6 bytes."""
    return Writer().u16(self.module).u16(self.node).u16(self.object).finish()

  @classmethod
  def unpack(cls, data: bytes) -> "Address":
    """Deserialize address from bytes."""
    r = Reader(data)
    return cls(module=r.u16(), node=r.u16(), object=r.u16())

  def __str__(self) -> str:
    return f"{self.module}:{self.node}:{self.object}"


@dataclass
class IpPacket:
  """Hamilton IpPacket2 - Transport layer.

  Structure:
  Bytes 00-01: size (2)
  Bytes 02:    protocol (1)
  Bytes 03:    version byte (major.minor)
  Bytes 04-05: options_length (2)
  Bytes 06+:   options (x bytes)
  Bytes:       payload
  """

  protocol: int  # Protocol identifier (6=OBJECT_DISCOVERY, 7=INITIALIZATION)
  payload: bytes
  options: bytes = b""

  def pack(self) -> bytes:
    """Serialize IP packet."""
    # Calculate size: protocol(1) + version(1) + opts_len(2) + options + payload
    packet_size = 1 + 1 + 2 + len(self.options) + len(self.payload)

    return (
      Writer()
      .u16(packet_size)
      .u8(self.protocol)
      .u8(encode_version_byte(HAMILTON_PROTOCOL_VERSION_MAJOR, HAMILTON_PROTOCOL_VERSION_MINOR))
      .u16(len(self.options))
      .raw_bytes(self.options)
      .raw_bytes(self.payload)
      .finish()
    )

  @classmethod
  def unpack(cls, data: bytes) -> "IpPacket":
    """Deserialize IP packet."""
    r = Reader(data)
    _size = r.u16()  # Read but unused
    protocol = r.u8()
    major, minor = decode_version_byte(r.u8())

    # Validate version
    if major != HAMILTON_PROTOCOL_VERSION_MAJOR or minor != HAMILTON_PROTOCOL_VERSION_MINOR:
      # Warning but not fatal
      pass

    opts_len = r.u16()
    options = r.raw_bytes(opts_len) if opts_len > 0 else b""
    payload = r.remaining()

    return cls(protocol=protocol, payload=payload, options=options)


@dataclass
class HarpPacket:
  """Hamilton HarpPacket2 - Protocol layer.

  Structure:
  Bytes 00-05: src address (module, node, object)
  Bytes 06-11: dst address (module, node, object)
  Byte  12:    sequence number
  Byte  13:    reserved
  Byte  14:    protocol (2=HOI, 3=Registration)
  Byte  15:    action
  Bytes 16-17: message length
  Bytes 18-19: options length
  Bytes 20+:   options
  Bytes:       version byte (major.minor)
  Byte:        reserved2
  Bytes:       payload
  """

  src: Address
  dst: Address
  seq: int
  protocol: int  # 2=HOI, 3=Registration
  action_code: int  # Base action code (0-15)
  payload: bytes
  options: bytes = b""
  response_required: bool = True  # Controls bit 4 of action byte

  @property
  def action(self) -> int:
    """Compute action byte from action_code and response_required flag.

    Returns:
      Action byte with bit 4 set if response required
    """
    return self.action_code | (0x10 if self.response_required else 0x00)

  def pack(self) -> bytes:
    """Serialize HARP packet."""
    # Message length includes: src(6) + dst(6) + seq(1) + reserved(1) + protocol(1) +
    #   action(1) + msg_len(2) + opts_len(2) + options + version(1) + reserved2(1) + payload
    # = 20 (fixed header) + options + version + reserved2 + payload
    msg_len = 20 + len(self.options) + 1 + 1 + len(self.payload)

    return (
      Writer()
      .raw_bytes(self.src.pack())
      .raw_bytes(self.dst.pack())
      .u8(self.seq)
      .u8(0)  # reserved
      .u8(self.protocol)
      .u8(self.action)  # Uses computed property
      .u16(msg_len)
      .u16(len(self.options))
      .raw_bytes(self.options)
      .u8(0)  # version byte - C# DLL uses 0, not 3.0
      .u8(0)  # reserved2
      .raw_bytes(self.payload)
      .finish()
    )

  @classmethod
  def unpack(cls, data: bytes) -> "HarpPacket":
    """Deserialize HARP packet."""
    r = Reader(data)

    # Parse addresses
    src = Address.unpack(r.raw_bytes(6))
    dst = Address.unpack(r.raw_bytes(6))

    seq = r.u8()
    _reserved = r.u8()  # Read but unused
    protocol = r.u8()
    action_byte = r.u8()
    _msg_len = r.u16()  # Read but unused
    opts_len = r.u16()

    options = r.raw_bytes(opts_len) if opts_len > 0 else b""
    _version = r.u8()  # version byte (C# DLL uses 0) - Read but unused
    _reserved2 = r.u8()  # Read but unused
    payload = r.remaining()

    # Decompose action byte into action_code and response_required flag
    action_code = action_byte & 0x0F
    response_required = bool(action_byte & 0x10)

    return cls(
      src=src,
      dst=dst,
      seq=seq,
      protocol=protocol,
      action_code=action_code,
      payload=payload,
      options=options,
      response_required=response_required,
    )


@dataclass
class HoiPacket:
  """Hamilton HoiPacket2 - HOI application layer.

  Structure:
      Byte  00:    interface_id
      Byte  01:    action
      Bytes 02-03: action_id
      Byte  04:    version byte (major.minor)
      Byte  05:    number of fragments
      Bytes 06+:   DataFragments

  Note: params must be DataFragment-wrapped (use HoiParams to build).
  """

  interface_id: int
  action_code: int  # Base action code (0-15)
  action_id: int
  params: bytes  # Already DataFragment-wrapped via HoiParams
  response_required: bool = False  # Controls bit 4 of action byte

  @property
  def action(self) -> int:
    """Compute action byte from action_code and response_required flag.

    Returns:
      Action byte with bit 4 set if response required
    """
    return self.action_code | (0x10 if self.response_required else 0x00)

  def pack(self) -> bytes:
    """Serialize HOI packet."""
    num_fragments = self._count_fragments(self.params)

    return (
      Writer()
      .u8(self.interface_id)
      .u8(self.action)  # Uses computed property
      .u16(self.action_id)
      .u8(0)  # version byte - always 0 for HOI packets (not 0x30!)
      .u8(num_fragments)
      .raw_bytes(self.params)
      .finish()
    )

  @classmethod
  def unpack(cls, data: bytes) -> "HoiPacket":
    """Deserialize HOI packet."""
    r = Reader(data)

    interface_id = r.u8()
    action_byte = r.u8()
    action_id = r.u16()
    major, minor = decode_version_byte(r.u8())
    _num_fragments = r.u8()  # Read but unused
    params = r.remaining()

    # Decompose action byte into action_code and response_required flag
    action_code = action_byte & 0x0F
    response_required = bool(action_byte & 0x10)

    return cls(
      interface_id=interface_id,
      action_code=action_code,
      action_id=action_id,
      params=params,
      response_required=response_required,
    )

  @staticmethod
  def _count_fragments(data: bytes) -> int:
    """Count DataFragments in params.

    Each DataFragment has format: [type_id:1][flags:1][length:2][data:n]
    """
    if len(data) == 0:
      return 0

    count = 0
    offset = 0

    while offset < len(data):
      if offset + 4 > len(data):
        break  # Not enough bytes for a fragment header

      # Read fragment length
      fragment_length = struct.unpack("<H", data[offset + 2 : offset + 4])[0]

      # Skip this fragment: header(4) + data(fragment_length)
      offset += 4 + fragment_length
      count += 1

    return count


@dataclass
class RegistrationPacket:
  """Hamilton RegistrationPacket2 - Registration protocol payload.

  Structure:
      Bytes 00-01: action code (2)
      Bytes 02-03: response code (2)
      Byte  04:    version byte (DLL uses 0x00, not 0x30)
      Byte  05:    reserved
      Bytes 06-11: req address (module, node, object)
      Bytes 12-17: res address (module, node, object)
      Bytes 18-19: options length (2)
      Bytes 20+:   options
  """

  action_code: int
  response_code: int
  req_address: Address
  res_address: Address
  options: bytes = b""

  def pack(self) -> bytes:
    """Serialize Registration packet."""
    return (
      Writer()
      .u16(self.action_code)
      .u16(self.response_code)
      .u8(0)  # version byte - DLL uses 0.0, not 3.0
      .u8(0)  # reserved
      .raw_bytes(self.req_address.pack())
      .raw_bytes(self.res_address.pack())
      .u16(len(self.options))
      .raw_bytes(self.options)
      .finish()
    )

  @classmethod
  def unpack(cls, data: bytes) -> "RegistrationPacket":
    """Deserialize Registration packet."""
    r = Reader(data)

    action_code = r.u16()
    response_code = r.u16()
    _version = r.u8()  # version byte (DLL uses 0, not packed 3.0) - Read but unused
    _reserved = r.u8()  # Read but unused
    req_address = Address.unpack(r.raw_bytes(6))
    res_address = Address.unpack(r.raw_bytes(6))
    opts_len = r.u16()
    options = r.raw_bytes(opts_len) if opts_len > 0 else b""

    return cls(
      action_code=action_code,
      response_code=response_code,
      req_address=req_address,
      res_address=res_address,
      options=options,
    )


@dataclass
class ConnectionPacket:
  """Hamilton ConnectionPacket - Connection initialization payload.

  Used for Protocol 7 (INITIALIZATION). Has a different structure than
  HARP-based packets - uses raw parameter encoding, NOT DataFragments.

  Structure:
  Byte  00:    version
  Byte  01:    message_id
  Byte  02:    count (number of parameters)
  Byte  03:    unknown
  Bytes 04+:   raw parameters [id|type|reserved|value] repeated
  """

  params: bytes  # Raw format (NOT DataFragments)

  def pack_into_ip(self) -> bytes:
    """Build complete IP packet for connection initialization.

    Returns full IP packet with protocol=7.
    """
    # Connection packet size: just the params (frame is included in params)
    packet_size = 1 + 1 + 2 + len(self.params)

    return (
      Writer()
      .u16(packet_size)
      .u8(7)  # INITIALIZATION protocol
      .u8(encode_version_byte(HAMILTON_PROTOCOL_VERSION_MAJOR, HAMILTON_PROTOCOL_VERSION_MINOR))
      .u16(0)  # options_length
      .raw_bytes(self.params)
      .finish()
    )

  @classmethod
  def unpack_from_ip_payload(cls, data: bytes) -> "ConnectionPacket":
    """Extract ConnectionPacket from IP packet payload.

    Assumes IP header has already been parsed.
    """
    return cls(params=data)
