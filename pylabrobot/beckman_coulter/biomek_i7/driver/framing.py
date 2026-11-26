"""ACE framing: how messages travel in the Biomek i7's USB transfers.

Every message travels as a frame, a length byte followed by that many message bytes::

  length (1) | message (length)

The host packs one or more frames into an interrupt OUT transfer on endpoint 0x02 (32-byte
packets) and sends no zero-length packet after one that fills its last packet. A board sends one
frame per interrupt IN transfer on endpoint 0x81 (16-byte packets); a frame longer than 15 bytes
spans several packets. Established on a cold start captured with USBPcap, 2026-10-24: all 3,507
transfers split into whole frames.
"""

from typing import Iterable, List

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import FrameError

# The largest OUT transfer the Master Controller sent in the captured cold start. Larger ones are
# not known to be accepted, so frames are packed no further than this.
MAX_OUT_TRANSFER = 253

# The IN endpoint's packet size: one read returns at most one packet.
IN_PACKET_SIZE = 16


def frame(message: bytes) -> bytes:
  """Prefix one message with its length byte.

  Raises:
    ValueError: if the message is empty or longer than 255 bytes.
  """
  if not 0 < len(message) <= 0xFF:
    raise ValueError(f"an ACE message is 1-255 bytes, got {len(message)}")
  return bytes([len(message)]) + message


def pack(messages: Iterable[bytes], max_transfer: int = MAX_OUT_TRANSFER) -> List[bytes]:
  """Pack messages, in order, into as few OUT transfers as fit `max_transfer` bytes each."""
  transfers: List[bytes] = []
  current = b""
  for message in messages:
    framed = frame(message)
    if len(framed) > max_transfer:
      raise ValueError(f"a {len(framed)}-byte frame does not fit a {max_transfer}-byte transfer")
    if len(current) + len(framed) > max_transfer:
      transfers.append(current)
      current = b""
    current += framed
  if current:
    transfers.append(current)
  return transfers


def split(transfer: bytes) -> List[bytes]:
  """Split one whole transfer into its messages.

  Raises:
    FrameError: if the transfer does not end on a frame boundary, or holds an empty frame.
  """
  reader = FrameReader()
  messages = reader.feed(transfer)
  if reader.pending:
    raise FrameError(f"transfer ends inside a frame: {transfer.hex()}")
  return messages


class FrameReader:
  """Reassemble frames from IN packets, which may end anywhere inside a frame."""

  def __init__(self) -> None:
    self._buffer = bytearray()

  @property
  def pending(self) -> int:
    """How many bytes of an unfinished frame are held."""
    return len(self._buffer)

  def feed(self, data: bytes) -> List[bytes]:
    """Take the next bytes read and return every message they complete.

    Raises:
      FrameError: on a zero length byte, which no board sends and which would never advance.
    """
    self._buffer += data
    messages: List[bytes] = []
    while self._buffer:
      length = self._buffer[0]
      if length == 0:
        bad = bytes(self._buffer)
        self._buffer.clear()
        raise FrameError(f"zero-length frame in {bad.hex()}")
      if len(self._buffer) < length + 1:
        break
      messages.append(bytes(self._buffer[1 : length + 1]))
      del self._buffer[: length + 1]
    return messages

  def reset(self) -> None:
    """Drop an unfinished frame, as after a read error."""
    self._buffer.clear()
