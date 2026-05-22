"""Agile motor controller 10-byte packet builder and parser.

The Bravo's Agile controller (Beckman AgileDrive) uses a fixed 10-byte binary
packet format with CRC8 checksums. These packets are transported through the
Rabbit microcontroller via CMD_DIRECT_AGILE_COMMAND (0xA1).

Packet layout (10 bytes):
  [0]    Header / command type
  [1]    Controller ID (always 0 for broadcast)
  [2-8]  Payload (varies by command type)
  [9]    CRC8 checksum

This module provides builder functions matching the Agile SDK API:
  RegisterGet, RegisterEqualValue, MoveAbsoluteValue, MoveRelativeValue,
  MoveJogValue, MoveGo, ServoEnable, ServoDisable, ResetFaults, GetGroupAStatus.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

AGILE_PACKET_SIZE = 10

# CRC8 lookup table (polynomial 0x07, used by Agile protocol)
_CRC8_TABLE = [
  0x00,
  0x07,
  0x0E,
  0x09,
  0x1C,
  0x1B,
  0x12,
  0x15,
  0x38,
  0x3F,
  0x36,
  0x31,
  0x24,
  0x23,
  0x2A,
  0x2D,
  0x70,
  0x77,
  0x7E,
  0x79,
  0x6C,
  0x6B,
  0x62,
  0x65,
  0x48,
  0x4F,
  0x46,
  0x41,
  0x54,
  0x53,
  0x5A,
  0x5D,
  0xE0,
  0xE7,
  0xEE,
  0xE9,
  0xFC,
  0xFB,
  0xF2,
  0xF5,
  0xD8,
  0xDF,
  0xD6,
  0xD1,
  0xC4,
  0xC3,
  0xCA,
  0xCD,
  0x90,
  0x97,
  0x9E,
  0x99,
  0x8C,
  0x8B,
  0x82,
  0x85,
  0xA8,
  0xAF,
  0xA6,
  0xA1,
  0xB4,
  0xB3,
  0xBA,
  0xBD,
  0xC7,
  0xC0,
  0xC9,
  0xCE,
  0xDB,
  0xDC,
  0xD5,
  0xD2,
  0xFF,
  0xF8,
  0xF1,
  0xF6,
  0xE3,
  0xE4,
  0xED,
  0xEA,
  0xB7,
  0xB0,
  0xB9,
  0xBE,
  0xAB,
  0xAC,
  0xA5,
  0xA2,
  0x8F,
  0x88,
  0x81,
  0x86,
  0x93,
  0x94,
  0x9D,
  0x9A,
  0x27,
  0x20,
  0x29,
  0x2E,
  0x3B,
  0x3C,
  0x35,
  0x32,
  0x1F,
  0x18,
  0x11,
  0x16,
  0x03,
  0x04,
  0x0D,
  0x0A,
  0x57,
  0x50,
  0x59,
  0x5E,
  0x4B,
  0x4C,
  0x45,
  0x42,
  0x6F,
  0x68,
  0x61,
  0x66,
  0x73,
  0x74,
  0x7D,
  0x7A,
  0x89,
  0x8E,
  0x87,
  0x80,
  0x95,
  0x92,
  0x9B,
  0x9C,
  0xB1,
  0xB6,
  0xBF,
  0xB8,
  0xAD,
  0xAA,
  0xA3,
  0xA4,
  0xF9,
  0xFE,
  0xF7,
  0xF0,
  0xE5,
  0xE2,
  0xEB,
  0xEC,
  0xC1,
  0xC6,
  0xCF,
  0xC8,
  0xDD,
  0xDA,
  0xD3,
  0xD4,
  0x69,
  0x6E,
  0x67,
  0x60,
  0x75,
  0x72,
  0x7B,
  0x7C,
  0x51,
  0x56,
  0x5F,
  0x58,
  0x4D,
  0x4A,
  0x43,
  0x44,
  0x19,
  0x1E,
  0x17,
  0x10,
  0x05,
  0x02,
  0x0B,
  0x0C,
  0x21,
  0x26,
  0x2F,
  0x28,
  0x3D,
  0x3A,
  0x33,
  0x34,
  0x4E,
  0x49,
  0x40,
  0x47,
  0x52,
  0x55,
  0x5C,
  0x5B,
  0x76,
  0x71,
  0x78,
  0x7F,
  0x6A,
  0x6D,
  0x64,
  0x63,
  0x3E,
  0x39,
  0x30,
  0x37,
  0x22,
  0x25,
  0x2C,
  0x2B,
  0x06,
  0x01,
  0x08,
  0x0F,
  0x1A,
  0x1D,
  0x14,
  0x13,
  0xAE,
  0xA9,
  0xA0,
  0xA7,
  0xB2,
  0xB5,
  0xBC,
  0xBB,
  0x96,
  0x91,
  0x98,
  0x9F,
  0x8A,
  0x8D,
  0x84,
  0x83,
  0xDE,
  0xD9,
  0xD0,
  0xD7,
  0xC2,
  0xC5,
  0xCC,
  0xCB,
  0xE6,
  0xE1,
  0xE8,
  0xEF,
  0xFA,
  0xFD,
  0xF4,
  0xF3,
]


def crc8(data: bytes | bytearray, length: int | None = None) -> int:
  """Compute CRC8 checksum over data bytes."""
  if length is None:
    length = len(data)
  crc = 0
  for i in range(length):
    crc = _CRC8_TABLE[crc ^ (data[i] & 0xFF)]
  return crc


def _make_packet(header: int, controller_id: int, payload: bytes) -> bytes:
  """Build a 10-byte Agile packet with CRC8."""
  pkt = bytearray(AGILE_PACKET_SIZE)
  pkt[0] = header & 0xFF
  pkt[1] = controller_id & 0xFF
  for i, b in enumerate(payload[:7]):
    pkt[2 + i] = b
  pkt[9] = crc8(pkt, 9)
  return bytes(pkt)


def verify_packet(packet: bytes) -> bool:
  """Verify CRC8 of a received 10-byte Agile packet."""
  if len(packet) != AGILE_PACKET_SIZE:
    return False
  return crc8(packet, 9) == packet[9]


# ---------------------------------------------------------------------------
# Agile command types (header byte values)
# ---------------------------------------------------------------------------


class AgileCommand:
  REGISTER_GET = 0x01
  REGISTER_SET = 0x02
  MOVE_ABSOLUTE = 0x10
  MOVE_RELATIVE = 0x11
  MOVE_JOG = 0x12
  MOVE_GO = 0x13
  SERVO_ENABLE = 0x20
  SERVO_DISABLE = 0x21
  RESET_FAULTS = 0x30
  GET_GROUP_A_STATUS = 0x40


# ---------------------------------------------------------------------------
# Common Agile registers
# ---------------------------------------------------------------------------


class AgileRegister:
  UNIQUE_VALUE = 0x0100  # A_Control_Unique_Value, expects 0xAA55
  POSITION = 0x0200  # Current position (ticks)
  VELOCITY = 0x0201  # Current velocity
  STATUS_A = 0x0300  # Group A status bits
  HOME_FLAG = 0x0400  # Home flag register
  POSITION_ERROR = 0x0500  # Position error
  MAX_POSITION_ERROR = 0x0501  # Maximum allowable position error
  SERVO_ENABLED = 0x0600  # Servo enable state
  # ADC registers for head detection calibration
  CORE_ADC0 = 0x0846  # 2118 decimal
  OFFSET_ADC0 = 0x0848  # 2120 decimal
  OFFSET_ADC3 = 0x0869  # 2153 decimal
  # CRC error tracking
  CRC_ERROR_COUNT = 0x095B


UNIQUE_VALUE_EXPECTED = 0xAA55


# ---------------------------------------------------------------------------
# Packet builder functions
# ---------------------------------------------------------------------------


def register_get(controller_id: int, register: int) -> bytes:
  """Build a RegisterGet packet to read a motor controller register."""
  payload = struct.pack("<H5x", register)[:7]
  return _make_packet(AgileCommand.REGISTER_GET, controller_id, payload)


def register_set_value(controller_id: int, register: int, value: int) -> bytes:
  """Build a RegisterEqualValue packet to write a register."""
  payload = struct.pack("<HI1x", register, value)[:7]
  return _make_packet(AgileCommand.REGISTER_SET, controller_id, payload)


def move_absolute_value(controller_id: int, axis: int, position_ticks: float) -> bytes:
  """Build a MoveAbsoluteValue packet to set an absolute destination."""
  payload = struct.pack("<Bf2x", axis, position_ticks)[:7]
  return _make_packet(AgileCommand.MOVE_ABSOLUTE, controller_id, payload)


def move_relative_value(controller_id: int, axis: int, delta_ticks: float) -> bytes:
  """Build a MoveRelativeValue packet."""
  payload = struct.pack("<Bf2x", axis, delta_ticks)[:7]
  return _make_packet(AgileCommand.MOVE_RELATIVE, controller_id, payload)


def move_jog_value(controller_id: int, axis: int, velocity: float) -> bytes:
  """Build a MoveJogValue packet to start a continuous jog."""
  payload = struct.pack("<Bf2x", axis, velocity)[:7]
  return _make_packet(AgileCommand.MOVE_JOG, controller_id, payload)


def move_go(controller_id: int, axis_mask: int) -> bytes:
  """Build a MoveGo packet to execute pending moves on specified axes."""
  payload = struct.pack("<B6x", axis_mask)[:7]
  return _make_packet(AgileCommand.MOVE_GO, controller_id, payload)


def servo_enable(controller_id: int, axis: int) -> bytes:
  """Build a ServoEnable packet."""
  payload = struct.pack("<B6x", axis)[:7]
  return _make_packet(AgileCommand.SERVO_ENABLE, controller_id, payload)


def servo_disable(controller_id: int, axis: int) -> bytes:
  """Build a ServoDisable packet."""
  payload = struct.pack("<B6x", axis)[:7]
  return _make_packet(AgileCommand.SERVO_DISABLE, controller_id, payload)


def reset_faults(controller_id: int, axis_mask: int) -> bytes:
  """Build a ResetFaults packet."""
  payload = struct.pack("<B6x", axis_mask)[:7]
  return _make_packet(AgileCommand.RESET_FAULTS, controller_id, payload)


def get_group_a_status(controller_id: int) -> bytes:
  """Build a GetGroupAStatus packet to read all axis statuses."""
  payload = b"\x00" * 7
  return _make_packet(AgileCommand.GET_GROUP_A_STATUS, controller_id, payload)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


@dataclass
class AgileReply:
  """Parsed Agile response packet."""

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
    """Extract a 32-bit register value from the response payload."""
    return struct.unpack_from("<I", self.payload, 2)[0]

  def get_float_value(self) -> float:
    """Extract a float value from the response payload."""
    return struct.unpack_from("<f", self.payload, 2)[0]
