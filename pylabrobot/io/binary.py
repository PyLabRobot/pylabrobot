"""Binary data reading utilities."""

import struct


class Reader:
  """A simple binary reader that tracks position and reads various data types."""

  def __init__(self, data: bytes, little_endian: bool = True):
    self._data = data
    self._offset = 0
    self._little_endian = little_endian

  def offset(self) -> int:
    """Return the current read offset."""
    return self._offset

  def has_remaining(self, n: int = 1) -> bool:
    """Check if at least n bytes remain."""
    return self._offset + n <= len(self._data)

  def remaining(self) -> int:
    """Return the number of bytes remaining."""
    return len(self._data) - self._offset

  def raw_bytes(self, length: int) -> bytes:
    """Read raw bytes and advance the offset."""
    if self._offset + length > len(self._data):
      raise ValueError(
        f"Not enough data: need {length} bytes at offset {self._offset}, "
        f"got {len(self._data) - self._offset}"
      )
    result = self._data[self._offset : self._offset + length]
    self._offset += length
    return result

  def _read(self, fmt: str, size: int) -> int:
    """Read a value using struct format."""
    prefix = "<" if self._little_endian else ">"
    data = self.raw_bytes(size)
    return int(struct.unpack(prefix + fmt, data)[0])

  def u8(self) -> int:
    """Read an unsigned 8-bit integer."""
    return self._read("B", 1)

  def i8(self) -> int:
    """Read a signed 8-bit integer."""
    return self._read("b", 1)

  def u16(self) -> int:
    """Read an unsigned 16-bit integer."""
    return self._read("H", 2)

  def i16(self) -> int:
    """Read a signed 16-bit integer."""
    return self._read("h", 2)

  def u32(self) -> int:
    """Read an unsigned 32-bit integer."""
    return self._read("I", 4)

  def i32(self) -> int:
    """Read a signed 32-bit integer."""
    return self._read("i", 4)

  def string(self, length: int, encoding: str = "utf-8") -> str:
    """Read a string of specified length."""
    return self.raw_bytes(length).decode(encoding, errors="ignore")

  def peek(self, length: int = 1) -> bytes:
    """Peek at bytes without advancing the offset."""
    if self._offset + length > len(self._data):
      raise ValueError(f"Not enough data to peek {length} bytes")
    return self._data[self._offset : self._offset + length]

  def skip(self, length: int) -> None:
    """Skip bytes without reading them."""
    if self._offset + length > len(self._data):
      raise ValueError(f"Cannot skip {length} bytes, only {self.remaining()} remaining")
    self._offset += length
