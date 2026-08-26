"""Agile packet codec for the Agile 7612 Bravo generation (CRC-8/MAXIM variant).

This wire format is not vendor protocol documentation. It was recovered by
observing traffic between Agilent VWorks and a Bravo Agile 7612 controller,
not from a published specification. The protocol has no authentication or
encryption: anyone with network access to the instrument's TCP port can send
it commands.

A drop-in replacement for :mod:`.agile_packet` with an identical API: same
10-byte packet layout, same command headers and register addresses, only the
checksum differs. Every packet builder here, and :class:`AgileReply`, use
:func:`~.agile_7612_crc.crc8_maxim` instead of the SMBUS CRC-8 that
:mod:`.agile_packet` uses.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .agile_7612_crc import crc8_maxim
from .agile_packet import AGILE_PACKET_SIZE, UNIQUE_VALUE_EXPECTED, AgileCommand, AgileRegister

__all__ = [
  "AGILE_PACKET_SIZE",
  "AgileCommand",
  "AgileRegister",
  "AgileReply",
  "UNIQUE_VALUE_EXPECTED",
  "crc8",
  "verify_packet",
  "register_get",
  "register_set_value",
  "move_absolute_value",
  "move_relative_value",
  "move_jog_value",
  "move_go",
  "servo_enable",
  "servo_disable",
  "reset_faults",
  "get_group_a_status",
]

crc8 = crc8_maxim


def _make_packet(header: int, controller_id: int, payload: bytes) -> bytes:
  """Build a 10-byte Agile packet with a CRC-8/MAXIM checksum.

  Args:
    header: The command-type byte.
    controller_id: The target controller ID, or 0 for broadcast.
    payload: Up to 7 payload bytes; shorter payloads leave the remainder zero.

  Returns:
    The packed 10-byte packet.
  """
  pkt = bytearray(AGILE_PACKET_SIZE)
  pkt[0] = header & 0xFF
  pkt[1] = controller_id & 0xFF
  for i, b in enumerate(payload[:7]):
    pkt[2 + i] = b
  pkt[9] = crc8_maxim(pkt, 9)
  return bytes(pkt)


def verify_packet(packet: bytes) -> bool:
  """Verify a received 10-byte Agile 7612 packet's CRC-8/MAXIM checksum.

  Args:
    packet: The packet to verify.

  Returns:
    True if ``packet`` is 10 bytes and its checksum byte matches.
  """
  if len(packet) != AGILE_PACKET_SIZE:
    return False
  return crc8_maxim(packet, 9) == packet[9]


def register_get(controller_id: int, register: int) -> bytes:
  """Build a RegisterGet packet to read a motor controller register.

  Args:
    controller_id: The target controller ID.
    register: The register address to read.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<H5x", register)[:7]
  return _make_packet(AgileCommand.REGISTER_GET, controller_id, payload)


def register_set_value(controller_id: int, register: int, value: int) -> bytes:
  """Build a RegisterEqualValue packet to write a motor controller register.

  Args:
    controller_id: The target controller ID.
    register: The register address to write.
    value: The 32-bit value to write.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<HI1x", register, value)[:7]
  return _make_packet(AgileCommand.REGISTER_SET, controller_id, payload)


def move_absolute_value(controller_id: int, axis: int, position_ticks: float) -> bytes:
  """Build a MoveAbsoluteValue packet to set an absolute destination.

  Args:
    controller_id: The target controller ID.
    axis: The local axis index on that controller.
    position_ticks: The destination, in encoder ticks.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<Bf2x", axis, position_ticks)[:7]
  return _make_packet(AgileCommand.MOVE_ABSOLUTE, controller_id, payload)


def move_relative_value(controller_id: int, axis: int, delta_ticks: float) -> bytes:
  """Build a MoveRelativeValue packet.

  Args:
    controller_id: The target controller ID.
    axis: The local axis index on that controller.
    delta_ticks: The move distance, in encoder ticks.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<Bf2x", axis, delta_ticks)[:7]
  return _make_packet(AgileCommand.MOVE_RELATIVE, controller_id, payload)


def move_jog_value(controller_id: int, axis: int, velocity: float) -> bytes:
  """Build a MoveJogValue packet to start a continuous jog.

  Args:
    controller_id: The target controller ID.
    axis: The local axis index on that controller.
    velocity: The jog velocity, in ticks/ms.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<Bf2x", axis, velocity)[:7]
  return _make_packet(AgileCommand.MOVE_JOG, controller_id, payload)


def move_go(controller_id: int, axis_mask: int) -> bytes:
  """Build a MoveGo packet to execute pending moves on the given axes.

  Args:
    controller_id: The target controller ID.
    axis_mask: Bitmask of local axis indices to start.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<B6x", axis_mask)[:7]
  return _make_packet(AgileCommand.MOVE_GO, controller_id, payload)


def servo_enable(controller_id: int, axis: int) -> bytes:
  """Build a ServoEnable packet.

  Args:
    controller_id: The target controller ID.
    axis: The local axis index on that controller.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<B6x", axis)[:7]
  return _make_packet(AgileCommand.SERVO_ENABLE, controller_id, payload)


def servo_disable(controller_id: int, axis: int) -> bytes:
  """Build a ServoDisable packet.

  Args:
    controller_id: The target controller ID.
    axis: The local axis index on that controller.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<B6x", axis)[:7]
  return _make_packet(AgileCommand.SERVO_DISABLE, controller_id, payload)


def reset_faults(controller_id: int, axis_mask: int) -> bytes:
  """Build a ResetFaults packet.

  Args:
    controller_id: The target controller ID.
    axis_mask: Bitmask of local axis indices to reset.

  Returns:
    The packed packet.
  """
  payload = struct.pack("<B6x", axis_mask)[:7]
  return _make_packet(AgileCommand.RESET_FAULTS, controller_id, payload)


def get_group_a_status(controller_id: int) -> bytes:
  """Build a GetGroupAStatus packet to read all axis statuses.

  Args:
    controller_id: The target controller ID.

  Returns:
    The packed packet.
  """
  payload = b"\x00" * 7
  return _make_packet(AgileCommand.GET_GROUP_A_STATUS, controller_id, payload)


@dataclass
class AgileReply:
  """A parsed Agile 7612 response packet.

  Attributes:
    header: The response's header/command-type byte.
    controller_id: The responding controller's ID.
    payload: The 7-byte payload (packet bytes 2-8).
    crc_valid: Whether the packet's checksum byte matched.
  """

  header: int
  controller_id: int
  payload: bytes
  crc_valid: bool

  @classmethod
  def from_packet(cls, packet: bytes) -> AgileReply:
    """Parse a 10-byte Agile 7612 response packet.

    Args:
      packet: The raw packet bytes.

    Returns:
      The parsed reply. Checking :attr:`crc_valid` is the caller's
      responsibility; this does not raise on a checksum mismatch.

    Raises:
      ValueError: If ``packet`` is not exactly :data:`AGILE_PACKET_SIZE` bytes.
    """
    if len(packet) != AGILE_PACKET_SIZE:
      raise ValueError(f"Expected {AGILE_PACKET_SIZE} bytes, got {len(packet)}")
    return cls(
      header=packet[0],
      controller_id=packet[1],
      payload=packet[2:9],
      crc_valid=verify_packet(packet),
    )

  def get_register_value(self) -> int:
    """Extract a 32-bit register value from the response payload.

    Returns:
      The decoded value.
    """
    (value,) = struct.unpack_from("<I", self.payload, 2)
    return int(value)

  def get_float_value(self) -> float:
    """Extract a float value from the response payload.

    Returns:
      The decoded value.
    """
    (value,) = struct.unpack_from("<f", self.payload, 2)
    return float(value)
