"""Primitive byte de/serialization. Nice wrapper around struct packing/unpacking.

This module provides low-level byte serialization/deserialization without any
protocol-specific wrapping.

Example:
  # Writing
  data = Writer().u8(1).u16(100).string("test").finish()

  # Reading
  reader = Reader(data)
  val1 = reader.u8()
  val2 = reader.u16()
  val3 = reader.string()
"""

from __future__ import annotations

import struct
from io import BytesIO


class Writer:
  """Raw byte writer.

  Provides fluent interface for building byte sequences.
  """

  def __init__(self, little_endian: bool = True):
    self._buffer = BytesIO()
    self._endian = "<" if little_endian else ">"

  def u8(self, value: int) -> "Writer":
    """Write unsigned 8-bit integer (0-255)."""
    self._buffer.write(struct.pack(f"{self._endian}B", value))
    return self

  def u16(self, value: int) -> "Writer":
    """Write unsigned 16-bit integer."""
    self._buffer.write(struct.pack(f"{self._endian}H", value))
    return self

  def u32(self, value: int) -> "Writer":
    """Write unsigned 32-bit integer."""
    self._buffer.write(struct.pack(f"{self._endian}I", value))
    return self

  def u64(self, value: int) -> "Writer":
    """Write unsigned 64-bit integer."""
    self._buffer.write(struct.pack(f"{self._endian}Q", value))
    return self

  def i8(self, value: int) -> "Writer":
    """Write signed 8-bit integer (-128 to 127)."""
    self._buffer.write(struct.pack(f"{self._endian}b", value))
    return self

  def i16(self, value: int) -> "Writer":
    """Write signed 16-bit integer."""
    self._buffer.write(struct.pack(f"{self._endian}h", value))
    return self

  def i32(self, value: int) -> "Writer":
    """Write signed 32-bit integer."""
    self._buffer.write(struct.pack(f"{self._endian}i", value))
    return self

  def i64(self, value: int) -> "Writer":
    """Write signed 64-bit integer."""
    self._buffer.write(struct.pack(f"{self._endian}q", value))
    return self

  def f32(self, value: float) -> "Writer":
    """Write 32-bit float."""
    self._buffer.write(struct.pack(f"{self._endian}f", value))
    return self

  def f64(self, value: float) -> "Writer":
    """Write 64-bit double."""
    self._buffer.write(struct.pack(f"{self._endian}d", value))
    return self

  def string(self, value: str) -> "Writer":
    """Write null-terminated UTF-8 string."""
    self._buffer.write(value.encode("utf-8"))
    self._buffer.write(b"\x00")
    return self

  def raw_bytes(self, value: bytes) -> "Writer":
    """Write raw bytes."""
    self._buffer.write(value)
    return self

  def finish(self) -> bytes:
    """Return the built byte sequence."""
    return self._buffer.getvalue()


class Reader:
  """Raw byte reader.

  Reads primitive values from byte sequences.
  """

  def __init__(self, data: bytes, little_endian: bool = True):
    self._data = data
    self._offset = 0
    self._endian = "<" if little_endian else ">"

  def u8(self) -> int:
    """Read unsigned 8-bit integer."""
    if self._offset + 1 > len(self._data):
      raise ValueError(f"Not enough data for u8 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}B", self._data[self._offset : self._offset + 1])[0]  # type: ignore[assignment]
    self._offset += 1
    return value

  def u16(self) -> int:
    """Read unsigned 16-bit integer."""
    if self._offset + 2 > len(self._data):
      raise ValueError(f"Not enough data for u16 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}H", self._data[self._offset : self._offset + 2])[0]  # type: ignore[assignment]
    self._offset += 2
    return value

  def u32(self) -> int:
    """Read unsigned 32-bit integer."""
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Not enough data for u32 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}I", self._data[self._offset : self._offset + 4])[0]  # type: ignore[assignment]
    self._offset += 4
    return value

  def u64(self) -> int:
    """Read unsigned 64-bit integer."""
    if self._offset + 8 > len(self._data):
      raise ValueError(f"Not enough data for u64 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}Q", self._data[self._offset : self._offset + 8])[0]  # type: ignore[assignment]
    self._offset += 8
    return value

  def i8(self) -> int:
    """Read signed 8-bit integer."""
    if self._offset + 1 > len(self._data):
      raise ValueError(f"Not enough data for i8 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}b", self._data[self._offset : self._offset + 1])[0]  # type: ignore[assignment]
    self._offset += 1
    return value

  def i16(self) -> int:
    """Read signed 16-bit integer."""
    if self._offset + 2 > len(self._data):
      raise ValueError(f"Not enough data for i16 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}h", self._data[self._offset : self._offset + 2])[0]  # type: ignore[assignment]
    self._offset += 2
    return value

  def i32(self) -> int:
    """Read signed 32-bit integer."""
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Not enough data for i32 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}i", self._data[self._offset : self._offset + 4])[0]  # type: ignore[assignment]
    self._offset += 4
    return value

  def i64(self) -> int:
    """Read signed 64-bit integer."""
    if self._offset + 8 > len(self._data):
      raise ValueError(f"Not enough data for i64 at offset {self._offset}")
    value: int = struct.unpack(f"{self._endian}q", self._data[self._offset : self._offset + 8])[0]  # type: ignore[assignment]
    self._offset += 8
    return value

  def f32(self) -> float:
    """Read 32-bit float."""
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Not enough data for f32 at offset {self._offset}")
    value: float = struct.unpack(f"{self._endian}f", self._data[self._offset : self._offset + 4])[0]  # type: ignore[assignment]
    self._offset += 4
    return value

  def f64(self) -> float:
    """Read 64-bit double."""
    if self._offset + 8 > len(self._data):
      raise ValueError(f"Not enough data for f64 at offset {self._offset}")
    value: float = struct.unpack(f"{self._endian}d", self._data[self._offset : self._offset + 8])[0]  # type: ignore[assignment]
    self._offset += 8
    return value

  def string(self) -> str:
    """Read null-terminated UTF-8 string."""
    # Find null terminator
    null_pos = self._data.find(b"\x00", self._offset)
    if null_pos == -1:
      raise ValueError(f"No null terminator found for string at offset {self._offset}")

    # Extract string (excluding null terminator)
    string_bytes = self._data[self._offset : null_pos]
    self._offset = null_pos + 1  # Move past null terminator

    return string_bytes.decode("utf-8")

  def raw_bytes(self, n: int) -> bytes:
    """Read n raw bytes."""
    if self._offset + n > len(self._data):
      raise ValueError(f"Not enough data for {n} bytes at offset {self._offset}")
    value = self._data[self._offset : self._offset + n]
    self._offset += n
    return value

  def remaining(self) -> bytes:
    """Return all remaining unread bytes."""
    remaining = self._data[self._offset :]
    self._offset = len(self._data)
    return remaining

  def has_remaining(self) -> bool:
    """Check if there are unread bytes."""
    return self._offset < len(self._data)

  def offset(self) -> int:
    """Get current read offset."""
    return self._offset
