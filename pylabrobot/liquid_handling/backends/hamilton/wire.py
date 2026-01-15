"""Hamilton TCP wire protocol - primitive byte serialization.

This module provides low-level byte serialization/deserialization without any
protocol-specific wrapping. DataFragment headers, Registration options, and
Connection parameters are handled by higher-level modules.

Example:
    # Writing
    data = Wire.write().u8(1).u16(100).string("test").finish()

    # Reading
    reader = Wire.read(data)
    val1 = reader.u8()
    val2 = reader.u16()
    val3 = reader.string()
"""

from __future__ import annotations

import struct
from io import BytesIO


class Writer:
  """Raw byte writer for Hamilton protocol primitives.

  Provides fluent interface for building byte sequences. All integers use
  little-endian encoding per Hamilton specification.
  """

  def __init__(self):
    self._buffer = BytesIO()

  def u8(self, value: int) -> "Writer":
    """Write unsigned 8-bit integer (0-255)."""
    if not 0 <= value <= 255:
      raise ValueError(f"u8 value must be 0-255, got {value}")
    self._buffer.write(struct.pack("<B", value))
    return self

  def u16(self, value: int) -> "Writer":
    """Write unsigned 16-bit integer (little-endian)."""
    if not 0 <= value <= 65535:
      raise ValueError(f"u16 value must be 0-65535, got {value}")
    self._buffer.write(struct.pack("<H", value))
    return self

  def u32(self, value: int) -> "Writer":
    """Write unsigned 32-bit integer (little-endian)."""
    if not 0 <= value <= 4294967295:
      raise ValueError(f"u32 value must be 0-4294967295, got {value}")
    self._buffer.write(struct.pack("<I", value))
    return self

  def u64(self, value: int) -> "Writer":
    """Write unsigned 64-bit integer (little-endian)."""
    if not 0 <= value <= 18446744073709551615:
      raise ValueError("u64 value out of range")
    self._buffer.write(struct.pack("<Q", value))
    return self

  def i8(self, value: int) -> "Writer":
    """Write signed 8-bit integer (-128 to 127)."""
    if not -128 <= value <= 127:
      raise ValueError(f"i8 value must be -128 to 127, got {value}")
    self._buffer.write(struct.pack("<b", value))
    return self

  def i16(self, value: int) -> "Writer":
    """Write signed 16-bit integer (little-endian)."""
    if not -32768 <= value <= 32767:
      raise ValueError(f"i16 value must be -32768 to 32767, got {value}")
    self._buffer.write(struct.pack("<h", value))
    return self

  def i32(self, value: int) -> "Writer":
    """Write signed 32-bit integer (little-endian)."""
    if not -2147483648 <= value <= 2147483647:
      raise ValueError("i32 value out of range")
    self._buffer.write(struct.pack("<i", value))
    return self

  def i64(self, value: int) -> "Writer":
    """Write signed 64-bit integer (little-endian)."""
    if not -9223372036854775808 <= value <= 9223372036854775807:
      raise ValueError("i64 value out of range")
    self._buffer.write(struct.pack("<q", value))
    return self

  def f32(self, value: float) -> "Writer":
    """Write 32-bit float (little-endian)."""
    self._buffer.write(struct.pack("<f", value))
    return self

  def f64(self, value: float) -> "Writer":
    """Write 64-bit double (little-endian)."""
    self._buffer.write(struct.pack("<d", value))
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

  def version_byte(self, major: int, minor: int) -> "Writer":
    """Write Hamilton version byte (two 4-bit fields packed into one byte).

    Args:
        major: Major version (0-15, stored in upper 4 bits)
        minor: Minor version (0-15, stored in lower 4 bits)

    Returns:
        Self for method chaining
    """
    if not 0 <= major <= 15:
      raise ValueError(f"major version must be 0-15, got {major}")
    if not 0 <= minor <= 15:
      raise ValueError(f"minor version must be 0-15, got {minor}")
    version_byte = (minor & 0xF) | ((major & 0xF) << 4)
    return self.u8(version_byte)

  def finish(self) -> bytes:
    """Return the built byte sequence."""
    return self._buffer.getvalue()


class Reader:
  """Raw byte reader for Hamilton protocol primitives.

  Reads primitive values from byte sequences. All integers use little-endian
  encoding per Hamilton specification.
  """

  def __init__(self, data: bytes):
    self._data = data
    self._offset = 0

  def u8(self) -> int:
    """Read unsigned 8-bit integer."""
    if self._offset + 1 > len(self._data):
      raise ValueError(f"Not enough data for u8 at offset {self._offset}")
    value: int = struct.unpack("<B", self._data[self._offset : self._offset + 1])[0]  # type: ignore[assignment]
    self._offset += 1
    return value

  def u16(self) -> int:
    """Read unsigned 16-bit integer (little-endian)."""
    if self._offset + 2 > len(self._data):
      raise ValueError(f"Not enough data for u16 at offset {self._offset}")
    value: int = struct.unpack("<H", self._data[self._offset : self._offset + 2])[0]  # type: ignore[assignment]
    self._offset += 2
    return value

  def u32(self) -> int:
    """Read unsigned 32-bit integer (little-endian)."""
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Not enough data for u32 at offset {self._offset}")
    value: int = struct.unpack("<I", self._data[self._offset : self._offset + 4])[0]  # type: ignore[assignment]
    self._offset += 4
    return value

  def u64(self) -> int:
    """Read unsigned 64-bit integer (little-endian)."""
    if self._offset + 8 > len(self._data):
      raise ValueError(f"Not enough data for u64 at offset {self._offset}")
    value: int = struct.unpack("<Q", self._data[self._offset : self._offset + 8])[0]  # type: ignore[assignment]
    self._offset += 8
    return value

  def i8(self) -> int:
    """Read signed 8-bit integer."""
    if self._offset + 1 > len(self._data):
      raise ValueError(f"Not enough data for i8 at offset {self._offset}")
    value: int = struct.unpack("<b", self._data[self._offset : self._offset + 1])[0]  # type: ignore[assignment]
    self._offset += 1
    return value

  def i16(self) -> int:
    """Read signed 16-bit integer (little-endian)."""
    if self._offset + 2 > len(self._data):
      raise ValueError(f"Not enough data for i16 at offset {self._offset}")
    value: int = struct.unpack("<h", self._data[self._offset : self._offset + 2])[0]  # type: ignore[assignment]
    self._offset += 2
    return value

  def i32(self) -> int:
    """Read signed 32-bit integer (little-endian)."""
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Not enough data for i32 at offset {self._offset}")
    value: int = struct.unpack("<i", self._data[self._offset : self._offset + 4])[0]  # type: ignore[assignment]
    self._offset += 4
    return value

  def i64(self) -> int:
    """Read signed 64-bit integer (little-endian)."""
    if self._offset + 8 > len(self._data):
      raise ValueError(f"Not enough data for i64 at offset {self._offset}")
    value: int = struct.unpack("<q", self._data[self._offset : self._offset + 8])[0]  # type: ignore[assignment]
    self._offset += 8
    return value

  def f32(self) -> float:
    """Read 32-bit float (little-endian)."""
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Not enough data for f32 at offset {self._offset}")
    value: float = struct.unpack("<f", self._data[self._offset : self._offset + 4])[0]  # type: ignore[assignment]
    self._offset += 4
    return value

  def f64(self) -> float:
    """Read 64-bit double (little-endian)."""
    if self._offset + 8 > len(self._data):
      raise ValueError(f"Not enough data for f64 at offset {self._offset}")
    value: float = struct.unpack("<d", self._data[self._offset : self._offset + 8])[0]  # type: ignore[assignment]
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

  def version_byte(self) -> tuple[int, int]:
    """Read Hamilton version byte and return (major, minor).

    Returns:
        Tuple of (major_version, minor_version), each 0-15
    """
    version_byte = self.u8()
    minor = version_byte & 0xF
    major = (version_byte >> 4) & 0xF
    return (major, minor)

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


class Wire:
  """Factory for creating Writer and Reader instances."""

  @staticmethod
  def write() -> Writer:
    """Create a new Writer for building byte sequences."""
    return Writer()

  @staticmethod
  def read(data: bytes) -> Reader:
    """Create a new Reader for parsing byte sequences."""
    return Reader(data)
