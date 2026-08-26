"""Gemini 8-byte packet codec and controller-tree addressing.

A packet is the unit of work the Gemini protocol exchanges with one node of
the Darwin controller tree: a GET or SET of a single subcommand value, or the
response to one. Packet layout::

    byte 0     src_addr    (dev_id << 6) | node_id
    byte 1     dest_addr   (dev_id << 6) | node_id
    byte 2     (msg_id << 4) | cmd_type   -- msg_id is 2 bits, cmd_type is 4 bits
    byte 3     sub_command
    bytes 4-7  cmd_val (big-endian uint32)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .enums import PACKET_SIZE, CommandTypes

# --- InstructionAddress -------------------------------------------------------


@dataclass(frozen=True)
class InstructionAddress:
  """A controller-tree address: 6-bit node ID plus 2-bit device ID, in one byte.

  Encoding: ``byte = (dev_id << 6) | (node_id & 0x3F)``.

  Attributes:
    node_id: The node's address on the controller tree, 0-63.
    dev_id: The device index within that node, 0-3.
  """

  node_id: int
  dev_id: int = 0

  def __post_init__(self) -> None:
    """Validate that both fields fit their wire-encoded bit widths.

    Raises:
      ValueError: If ``node_id`` or ``dev_id`` is out of its valid range.
    """
    if not 0 <= self.node_id <= 0x3F:
      raise ValueError(f"node_id {self.node_id} out of range 0..63")
    if not 0 <= self.dev_id <= 0x03:
      raise ValueError(f"dev_id {self.dev_id} out of range 0..3")

  @property
  def byte(self) -> int:
    """The single-byte wire encoding of this address."""
    return ((self.dev_id & 0x03) << 6) | (self.node_id & 0x3F)

  @classmethod
  def from_byte(cls, b: int) -> InstructionAddress:
    """Decode an address from its single-byte wire encoding.

    Args:
      b: The encoded address byte.

    Returns:
      The decoded address.
    """
    return cls(node_id=b & 0x3F, dev_id=(b >> 6) & 0x03)

  def __str__(self) -> str:
    """Return ``"node.device"``, e.g. ``"4.1"``."""
    return f"{self.node_id}.{self.dev_id}"


HOST_ADDRESS = InstructionAddress(0, 0)
MASTER_ADDRESS = InstructionAddress(1, 0)
BROADCAST_ADDRESS = InstructionAddress(63, 0)


# --- Packet -------------------------------------------------------------------


@dataclass
class Packet:
  """One 8-byte Gemini packet.

  Attributes:
    src: Sending controller-tree address.
    dest: Destination controller-tree address.
    cmd_type: One of :class:`~.enums.CommandTypes`.
    sub_command: The subcommand this packet addresses.
    cmd_val: The 32-bit command value: a value to write for SET, the read
      result for a GET response, or a NAK code for an error response.
    msg_id: A 2-bit rotating counter (0-3) that correlates a SETCMD/GETCMD
      with its response; encoded into the high nibble of byte 2.
  """

  src: InstructionAddress
  dest: InstructionAddress
  cmd_type: int
  sub_command: int
  cmd_val: int = 0
  msg_id: int = 0

  def __post_init__(self) -> None:
    """Validate that every field fits its wire-encoded bit width.

    Raises:
      ValueError: If any field is out of its valid range.
    """
    if not 0 <= self.cmd_type <= 0x0F:
      raise ValueError(f"cmd_type {self.cmd_type} out of range 0..15")
    if not 0 <= self.msg_id <= 0x03:
      raise ValueError(f"msg_id {self.msg_id} out of range 0..3")
    if not 0 <= self.sub_command <= 0xFF:
      raise ValueError(f"sub_command {self.sub_command} out of range 0..255")
    if not 0 <= self.cmd_val <= 0xFFFFFFFF:
      raise ValueError(f"cmd_val {self.cmd_val} out of range 0..2^32-1")

  def to_bytes(self) -> bytes:
    """Pack this packet into its 8-byte wire encoding.

    Returns:
      The packed packet bytes.
    """
    b2 = ((self.msg_id & 0x03) << 4) | (self.cmd_type & 0x0F)
    return struct.pack(
      ">BBBBI",
      self.src.byte,
      self.dest.byte,
      b2,
      self.sub_command & 0xFF,
      self.cmd_val & 0xFFFFFFFF,
    )

  @classmethod
  def from_bytes(cls, data: bytes) -> Packet:
    """Parse a packet from its 8-byte wire encoding.

    Reserved bits 6-7 of byte 2 are dropped: they are not part of
    ``cmd_type`` or ``msg_id`` and are not preserved on re-encoding.

    Args:
      data: Exactly :data:`~.enums.PACKET_SIZE` bytes.

    Returns:
      The decoded packet.

    Raises:
      ValueError: If ``data`` is not exactly :data:`~.enums.PACKET_SIZE` bytes.
    """
    if len(data) != PACKET_SIZE:
      raise ValueError(f"Packet requires exactly {PACKET_SIZE} bytes, got {len(data)}")
    src, dest, b2, sub, val = struct.unpack(">BBBBI", data)
    return cls(
      src=InstructionAddress.from_byte(src),
      dest=InstructionAddress.from_byte(dest),
      cmd_type=b2 & 0x0F,
      sub_command=sub,
      cmd_val=val,
      msg_id=(b2 >> 4) & 0x03,
    )

  # Convenience constructors -------------------------------------------------

  @classmethod
  def get_request(
    cls,
    dest: InstructionAddress,
    sub_command: int,
    msg_id: int = 0,
    src: InstructionAddress = HOST_ADDRESS,
  ) -> Packet:
    """Build a GETCMD packet requesting a subcommand's current value.

    Args:
      dest: The controller-tree node to query.
      sub_command: The subcommand to read.
      msg_id: The rotating correlation counter.
      src: The sending address; defaults to the host.

    Returns:
      The GET packet.
    """
    return cls(
      src=src,
      dest=dest,
      cmd_type=CommandTypes.GETCMD,
      sub_command=sub_command,
      msg_id=msg_id,
    )

  @classmethod
  def set_request(
    cls,
    dest: InstructionAddress,
    sub_command: int,
    value: int,
    msg_id: int = 0,
    src: InstructionAddress = HOST_ADDRESS,
  ) -> Packet:
    """Build a SETCMD packet writing a subcommand's value.

    Args:
      dest: The controller-tree node to write to.
      sub_command: The subcommand to set.
      value: The 32-bit value to write.
      msg_id: The rotating correlation counter.
      src: The sending address; defaults to the host.

    Returns:
      The SET packet.
    """
    return cls(
      src=src,
      dest=dest,
      cmd_type=CommandTypes.SETCMD,
      sub_command=sub_command,
      cmd_val=value & 0xFFFFFFFF,
      msg_id=msg_id,
    )

  def is_response(self) -> bool:
    """Return whether this packet is any kind of SET/GET response."""
    return self.cmd_type in (
      CommandTypes.SETCMD_RESP,
      CommandTypes.GETCMD_RESP,
      CommandTypes.SETCMD_ERR_RESP,
      CommandTypes.GETCMD_ERR_RESP,
    )

  def is_error(self) -> bool:
    """Return whether this packet is a ``*_ERR_RESP`` (NAK) response."""
    return self.cmd_type in (
      CommandTypes.SETCMD_ERR_RESP,
      CommandTypes.GETCMD_ERR_RESP,
    )
