"""EL406 protocol framing utilities.

This module contains the protocol framing functions for building
properly formatted messages for the BioTek EL406 plate washer.
"""

from __future__ import annotations

from .constants import (
  MSG_CONSTANT,
  MSG_HEADER_SIZE,
  MSG_START_MARKER,
  MSG_VERSION_MARKER,
)


def build_framed_message(command: int, data: bytes = b"") -> bytes:
  """Build a properly framed EL406 message.

  Protocol structure:
    [0]: 0x01 (start marker)
    [1]: 0x02 (version marker)
    [2-3]: command (little-endian short)
    [4]: 0x01 (constant)
    [5-6]: reserved (ushort, typically 0)
    [7-8]: data length (ushort, little-endian)
    [9-10]: checksum (ushort, little-endian)
    ... followed by data bytes

  Checksum is two's complement of sum of header bytes 0-8 + all data bytes.

  Args:
    command: 16-bit command code
    data: Optional data bytes

  Returns:
    Complete framed message with header and checksum
  """
  header = bytearray(MSG_HEADER_SIZE)
  header[0] = MSG_START_MARKER
  header[1] = MSG_VERSION_MARKER
  header[2] = command & 0xFF  # Command low byte
  header[3] = (command >> 8) & 0xFF  # Command high byte
  header[4] = MSG_CONSTANT
  header[5] = 0x00  # Reserved low
  header[6] = 0x00  # Reserved high
  header[7] = len(data) & 0xFF  # Data length low
  header[8] = (len(data) >> 8) & 0xFF  # Data length high

  # Calculate checksum
  # Sum of header bytes 0-8 + all data bytes, then two's complement
  checksum_sum = sum(header[:9]) + sum(data)
  checksum = (0xFFFF - checksum_sum + 1) & 0xFFFF

  header[9] = checksum & 0xFF  # Checksum low
  header[10] = (checksum >> 8) & 0xFF  # Checksum high

  return bytes(header) + data
