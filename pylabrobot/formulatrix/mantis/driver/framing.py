"""Mantis wire framing, on interface B of the device's FTDI bridge.

Every frame, in both directions, is little-endian::

  size (4) | packet number (2) | 0 (2) | board (2) | code (2) | payload (n) | check word (2)

``size`` is 12 + n: the header and the payload, not the check word. The check word is the XOR of
the frame's 16-bit words before it. A host frame's code is its command. A device frame's code is 0
for a reply, which echoes the command's packet number and board, and the event number for an event.
"""

import logging
import struct
from typing import List, Tuple

from .errors import ChecksumError, FrameError

logger = logging.getLogger(__name__)

HEADER_SIZE = 12
CHECK_WORD_SIZE = 2
FIRST_EVENT_CODE = 0x0200
# PyLabRobot design choice: a 512-byte payload at most. The largest frame seen is 92 bytes.
MAXIMUM_FRAME_SIZE = HEADER_SIZE + 512 + CHECK_WORD_SIZE

MOTION_BOARD = 0
PRESSURE_BOARD = 10
ABSOLUTE_POSITION_SENSOR_BOARD = 11
LC3_BOARD = 12


def compute_check_word(data: bytes) -> int:
  """The XOR of the 16-bit little-endian words of ``data``.

  Raises:
    ValueError: if ``data`` has an odd length.
  """
  if len(data) % 2 != 0:
    raise ValueError(f"a check word covers whole 16-bit words, got {len(data)} bytes")
  check_word = 0
  for offset in range(0, len(data), 2):
    check_word ^= int.from_bytes(data[offset : offset + 2], "little")
  return check_word


def build_frame(*, packet_number: int, board: int, command: int, payload: bytes = b"") -> bytes:
  """Frame a command for the device.

  Args:
    packet_number: the command's packet number, 0 to 0xFFFF; the reply echoes it.
    board: the board the command goes to.
    command: the command number.
    payload: the command's arguments, a whole number of 16-bit words.
  """
  if len(payload) % 2 != 0:
    raise ValueError(f"a payload is a whole number of 16-bit words, got {len(payload)} bytes")
  frame = (
    (HEADER_SIZE + len(payload)).to_bytes(4, "little")
    + packet_number.to_bytes(2, "little")
    + bytes(2)
    + board.to_bytes(2, "little")
    + command.to_bytes(2, "little")
    + payload
  )
  return frame + compute_check_word(data=frame).to_bytes(2, "little")


def parse_frame(frame: bytes) -> Tuple[int, int, int, bytes]:
  """Check one whole frame and split it into packet number, board, code and payload.

  A reserved word other than 0, never seen from the device, is logged.

  Raises:
    FrameError: if the size field or the length is wrong.
    ChecksumError: if the check word does not match.
  """
  if len(frame) < HEADER_SIZE + CHECK_WORD_SIZE:
    raise FrameError(f"frame too short ({len(frame)} bytes): {frame.hex()}")
  size, packet_number, reserved, board, code = struct.unpack_from("<IHHHH", frame)
  if size + CHECK_WORD_SIZE != len(frame):
    raise FrameError(
      f"size field says {size} + {CHECK_WORD_SIZE} bytes, got {len(frame)}: {frame.hex()}"
    )
  if reserved != 0:
    logger.warning("reserved word is 0x%04x, not 0: %s", reserved, frame.hex())
  (received,) = struct.unpack_from("<H", frame, size)
  computed = compute_check_word(data=frame[:size])
  if received != computed:
    raise ChecksumError(f"check word 0x{received:04x}, computed 0x{computed:04x}: {frame.hex()}")
  return packet_number, board, code, frame[HEADER_SIZE:size]


def split_frames(buffer: bytearray) -> List[bytes]:
  """Take every whole frame off the front of ``buffer``, leaving a partial one in it.

  Frames follow each other with no separator, so each one's size field says where the next starts.
  A size field no frame can have (below the header, odd, or over ``MAXIMUM_FRAME_SIZE``) is not a
  frame start: one byte is dropped and the next is tried, and the bytes dropped are logged.
  """
  frames: List[bytes] = []
  dropped = bytearray()
  while len(buffer) >= 4:
    (size,) = struct.unpack_from("<I", buffer)
    if size < HEADER_SIZE or size % 2 != 0 or size + CHECK_WORD_SIZE > MAXIMUM_FRAME_SIZE:
      dropped.append(buffer.pop(0))
      continue
    if len(buffer) < size + CHECK_WORD_SIZE:
      break
    frames.append(bytes(buffer[: size + CHECK_WORD_SIZE]))
    del buffer[: size + CHECK_WORD_SIZE]
  if dropped:
    logger.warning("dropped %d bytes that start no frame: %s", len(dropped), dropped.hex())
  return frames


def decode_string(payload: bytes) -> str:
  """A UTF-16LE string ending in a 0x0000 character, as in a version or name reply.

  Raises:
    FrameError: if the payload has no terminating 0x0000 character.
  """
  for offset in range(0, len(payload) - 1, 2):
    if payload[offset : offset + 2] == bytes(2):
      return payload[:offset].decode("utf-16-le")
  raise FrameError(f"string without a terminating 0x0000: {payload.hex()}")
