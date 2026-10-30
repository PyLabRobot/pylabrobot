"""BMG Labtech FTDI wire framing, the same on BMG's readers.

One frame per message, 8 bytes of overhead::

  STX (1) | size (2 BE, whole frame) | header (1) | payload (n) | checksum (3 BE) | CR (1)

The checksum is the 24-bit sum of every byte from STX through the end of the payload. The header
byte is set per reader (0x0C on the CLARIOstar).
"""

from .errors import ChecksumError, FrameError

FTDI_STX = 0x02
FTDI_CR = 0x0D
FTDI_OVERHEAD = 8  # STX + size(2) + header + checksum(3) + CR


def ftdi_wrap_payload(payload: bytes, *, header: int) -> bytes:
  """Frame a logical payload for the FTDI interface, with the reader's header byte."""
  frame = bytearray([FTDI_STX])
  frame.extend((len(payload) + FTDI_OVERHEAD).to_bytes(2, "big"))
  frame.append(header)
  frame.extend(payload)
  frame.extend((sum(frame) & 0xFFFFFF).to_bytes(3, "big"))
  frame.append(FTDI_CR)
  return bytes(frame)


def ftdi_validate_frame(frame: bytes, *, header: int) -> None:
  """Check an FTDI frame's delimiters, header byte, size field and checksum.

  Raises:
    FrameError: on a wrong STX, CR, header or size field.
    ChecksumError: on a checksum mismatch.
  """
  if len(frame) < FTDI_OVERHEAD:
    raise FrameError(f"Reply too short ({len(frame)} bytes): {frame.hex()}")
  if frame[0] != FTDI_STX:
    raise FrameError(f"Expected STX 0x{FTDI_STX:02x}, got 0x{frame[0]:02x}")
  if frame[-1] != FTDI_CR:
    raise FrameError(f"Expected CR 0x{FTDI_CR:02x}, got 0x{frame[-1]:02x}")
  if frame[3] != header:
    raise FrameError(f"Expected header 0x{header:02x} at byte 3, got 0x{frame[3]:02x}")
  size = int.from_bytes(frame[1:3], "big")
  if size != len(frame):
    raise FrameError(f"Size field says {size} bytes, got {len(frame)}")
  received = int.from_bytes(frame[-4:-1], "big")
  computed = sum(frame[:-4]) & 0xFFFFFF
  if received != computed:
    raise ChecksumError(f"Checksum 0x{received:06x}, computed 0x{computed:06x}")


def ftdi_extract_payload(frame: bytes) -> bytes:
  """Return the logical payload of a validated FTDI frame."""
  return frame[4:-4]
