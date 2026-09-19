"""The frame every command is sent in.

An eleven-byte header followed by a payload::

    [0]      start marker
    [1]      version marker
    [2-3]    command number
    [4]      constant
    [5-6]    reserved
    [7-8]    payload length
    [9-10]   checksum
    [11..]   payload

Every multi-byte field is little-endian, and the checksum covers the first nine header bytes plus
the whole payload.
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER_LENGTH = 11
START_MARKER = 0x01
VERSION_MARKER = 0x02
HEADER_CONSTANT = 0x01

STATUS_LENGTH = 2
"""How many bytes of a reply payload are the instrument's status word."""


def checksum(header: bytes, payload: bytes = b"") -> int:
  """The frame checksum.

  Args:
    header: The header, whose first nine bytes are covered.
    payload: The payload, all of which is covered.

  Returns:
    The two's complement of the sum, truncated to sixteen bits.
  """
  return -(sum(header[:9]) + sum(payload)) & 0xFFFF


@dataclass
class Header:
  """The eleven bytes in front of a payload.

  Attributes:
    number: Which command this is.
    payload_length: How many bytes follow the header.
    check: The frame checksum.
    reserved: Reserved, zero in every frame seen so far.
    start: The start marker.
    version: The version marker.
    constant: A byte that is the same in every frame.
  """

  number: int = 0
  payload_length: int = 0
  check: int = 0
  reserved: int = 0
  start: int = START_MARKER
  version: int = VERSION_MARKER
  constant: int = HEADER_CONSTANT

  def to_bytes(self) -> bytes:
    """Pack the header.

    Returns:
      Eleven bytes.
    """
    raw = bytearray(HEADER_LENGTH)
    raw[0] = self.start
    raw[1] = self.version
    raw[2:4] = self.number.to_bytes(2, "little")
    raw[4] = self.constant
    raw[5:7] = self.reserved.to_bytes(2, "little")
    raw[7:9] = self.payload_length.to_bytes(2, "little")
    raw[9:11] = self.check.to_bytes(2, "little")
    return bytes(raw)

  @classmethod
  def from_bytes(cls, raw: bytes) -> Header:
    """Unpack a header.

    Args:
      raw: Eleven bytes.

    Returns:
      The header.
    """
    return cls(
      start=raw[0],
      version=raw[1],
      number=int.from_bytes(raw[2:4], "little"),
      constant=raw[4],
      reserved=int.from_bytes(raw[5:7], "little"),
      payload_length=int.from_bytes(raw[7:9], "little"),
      check=int.from_bytes(raw[9:11], "little"),
    )
