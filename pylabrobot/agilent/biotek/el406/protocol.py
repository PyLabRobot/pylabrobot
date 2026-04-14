"""EL406 protocol framing utilities.

This module contains the protocol framing functions for building
properly formatted messages for the BioTek EL406 plate washer.
"""

from __future__ import annotations

from pylabrobot.io.binary import Writer


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
  # Build header bytes 0-8 (checksum placeholder filled after)
  header_prefix = (
    Writer()
    .u8(0x01)      # [0] Start marker
    .u8(0x02)    # [1] Version marker
    .u16(command)              # [2-3] Command (LE)
    .u8(0x01)          # [4] Constant
    .u16(0x0000)               # [5-6] Reserved
    .u16(len(data))            # [7-8] Data length (LE)
    .finish()
  )  # fmt: skip

  # Checksum: two's complement of sum of header bytes 0-8 + all data bytes
  checksum_sum = sum(header_prefix) + sum(data)
  checksum = (0xFFFF - checksum_sum + 1) & 0xFFFF

  return header_prefix + Writer().u16(checksum).finish() + data


def encode_column_mask(columns: list[int] | None) -> bytes:
  """Encode list of column indices to 6-byte (48-bit) column mask.

  Each bit represents one column: 0 = skip, 1 = operate on column.

  Args:
    columns: List of column indices (0-47) to select, or None for all columns.
      If None, returns all 1s (all columns selected).
      If empty list, returns all 0s (no columns selected).

  Returns:
    6 bytes representing the 48-bit column mask in little-endian order.

  Raises:
    ValueError: If any column index is out of range (not 0-47).
  """
  if columns is None:
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])

  for col in columns:
    if col < 0 or col > 47:
      raise ValueError(f"Column index {col} out of range. Must be 0-47.")

  mask = [0] * 6
  for col in columns:
    byte_index = col // 8
    bit_index = col % 8
    mask[byte_index] |= 1 << bit_index

  return bytes(mask)


def columns_to_column_mask(columns: list[int] | None, plate_wells: int = 96) -> list[int] | None:
  """Convert 1-indexed column numbers to 0-indexed column indices.

  Args:
    columns: List of column numbers (1-based), or None for all columns.
    plate_wells: Plate format (96, 384, 1536). Determines max columns.

  Returns:
    List of 0-indexed column indices, or None if columns is None.

  Raises:
    ValueError: If column numbers are out of range.
  """
  if columns is None:
    return None

  max_cols = {96: 12, 384: 24, 1536: 48}.get(plate_wells, 48)
  indices = []
  for col in columns:
    if col < 1 or col > max_cols:
      raise ValueError(f"Column {col} out of range for {plate_wells}-well plate (1-{max_cols}).")
    indices.append(col - 1)
  return indices
