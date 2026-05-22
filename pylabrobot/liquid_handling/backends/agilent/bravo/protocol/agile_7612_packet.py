"""Agile packet builder for Agile 7612 Bravo — uses CRC-8/MAXIM instead of SMBUS.

Drop-in replacement for ``agile_packet`` with identical API. All packet builder
functions and the ``AgileReply`` parser use CRC-8/MAXIM checksums.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_7612_crc import crc8_maxim
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_packet import (
  AGILE_PACKET_SIZE,
  AgileCommand,
  AgileRegister,
  UNIQUE_VALUE_EXPECTED,
)

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
  pkt = bytearray(AGILE_PACKET_SIZE)
  pkt[0] = header & 0xFF
  pkt[1] = controller_id & 0xFF
  for i, b in enumerate(payload[:7]):
    pkt[2 + i] = b
  pkt[9] = crc8_maxim(pkt, 9)
  return bytes(pkt)


def verify_packet(packet: bytes) -> bool:
  if len(packet) != AGILE_PACKET_SIZE:
    return False
  return crc8_maxim(packet, 9) == packet[9]


def register_get(controller_id: int, register: int) -> bytes:
  payload = struct.pack("<H5x", register)[:7]
  return _make_packet(AgileCommand.REGISTER_GET, controller_id, payload)


def register_set_value(controller_id: int, register: int, value: int) -> bytes:
  payload = struct.pack("<HI1x", register, value)[:7]
  return _make_packet(AgileCommand.REGISTER_SET, controller_id, payload)


def move_absolute_value(controller_id: int, axis: int, position_ticks: float) -> bytes:
  payload = struct.pack("<Bf2x", axis, position_ticks)[:7]
  return _make_packet(AgileCommand.MOVE_ABSOLUTE, controller_id, payload)


def move_relative_value(controller_id: int, axis: int, delta_ticks: float) -> bytes:
  payload = struct.pack("<Bf2x", axis, delta_ticks)[:7]
  return _make_packet(AgileCommand.MOVE_RELATIVE, controller_id, payload)


def move_jog_value(controller_id: int, axis: int, velocity: float) -> bytes:
  payload = struct.pack("<Bf2x", axis, velocity)[:7]
  return _make_packet(AgileCommand.MOVE_JOG, controller_id, payload)


def move_go(controller_id: int, axis_mask: int) -> bytes:
  payload = struct.pack("<B6x", axis_mask)[:7]
  return _make_packet(AgileCommand.MOVE_GO, controller_id, payload)


def servo_enable(controller_id: int, axis: int) -> bytes:
  payload = struct.pack("<B6x", axis)[:7]
  return _make_packet(AgileCommand.SERVO_ENABLE, controller_id, payload)


def servo_disable(controller_id: int, axis: int) -> bytes:
  payload = struct.pack("<B6x", axis)[:7]
  return _make_packet(AgileCommand.SERVO_DISABLE, controller_id, payload)


def reset_faults(controller_id: int, axis_mask: int) -> bytes:
  payload = struct.pack("<B6x", axis_mask)[:7]
  return _make_packet(AgileCommand.RESET_FAULTS, controller_id, payload)


def get_group_a_status(controller_id: int) -> bytes:
  payload = b"\x00" * 7
  return _make_packet(AgileCommand.GET_GROUP_A_STATUS, controller_id, payload)


@dataclass
class AgileReply:
  header: int
  controller_id: int
  payload: bytes
  crc_valid: bool

  @classmethod
  def from_packet(cls, packet: bytes) -> AgileReply:
    if len(packet) != AGILE_PACKET_SIZE:
      raise ValueError(f"Expected {AGILE_PACKET_SIZE} bytes, got {len(packet)}")
    return cls(
      header=packet[0],
      controller_id=packet[1],
      payload=packet[2:9],
      crc_valid=verify_packet(packet),
    )

  def get_register_value(self) -> int:
    return struct.unpack_from("<I", self.payload, 2)[0]

  def get_float_value(self) -> float:
    return struct.unpack_from("<f", self.payload, 2)[0]
