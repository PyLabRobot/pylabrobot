"""CLARIOstar wire framing for both interfaces.

Both interfaces carry the same logical payloads (command family, command, parameters); only
the framing around them differs.

FTDI (VID 0x0403 / PID 0xBB68), one frame per message, 8 bytes of overhead::

  STX (1) | size (2 BE, whole frame) | 0x0C (1) | payload (n) | checksum (3 BE) | CR (1)

The checksum is the 24-bit sum of every byte from STX through the end of the payload.

USB (VID 0x0483 / PID 0xA29A), 64-byte packets, each with a 7-byte header::

  0x00 0x0C | type (1) | 3 bytes | payload (up to 57)

  type 0x01: a whole message; byte 6 is the payload length.
  type 0x02: first packet of a longer message; bytes 5-6 are the total payload length (BE).
  type 0x03: a continuation packet; byte 6 is its sequence number, counting from 1.
  type 0x04: an acknowledgement, sent before the continuation packets of a long message.
"""

from typing import List

from .errors import ChecksumError, FrameError

# -- FTDI --------------------------------------------------------------------

FTDI_STX = 0x02
FTDI_HEADER = 0x0C
FTDI_CR = 0x0D
FTDI_OVERHEAD = 8  # STX + size(2) + header + checksum(3) + CR


def ftdi_wrap_payload(payload: bytes) -> bytes:
  """Frame a logical payload for the FTDI interface."""
  frame = bytearray([FTDI_STX])
  frame.extend((len(payload) + FTDI_OVERHEAD).to_bytes(2, "big"))
  frame.append(FTDI_HEADER)
  frame.extend(payload)
  frame.extend((sum(frame) & 0xFFFFFF).to_bytes(3, "big"))
  frame.append(FTDI_CR)
  return bytes(frame)


def ftdi_validate_frame(frame: bytes) -> None:
  """Check an FTDI frame's delimiters, size field and checksum.

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
  if frame[3] != FTDI_HEADER:
    raise FrameError(f"Expected header 0x{FTDI_HEADER:02x} at byte 3, got 0x{frame[3]:02x}")
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


# -- USB ---------------------------------------------------------------------

USB_PACKET_SIZE = 64
USB_HEADER_SIZE = 7
USB_CHUNK_SIZE = USB_PACKET_SIZE - USB_HEADER_SIZE

USB_SINGLE = 0x01
USB_FIRST = 0x02
USB_CONTINUATION = 0x03
USB_ACK = 0x04

# The host's acknowledgement of a multi-packet reply, asking for its continuation packets.
USB_HOST_ACK = bytes([0x00, FTDI_HEADER, USB_ACK, 0x01])


def usb_wrap_payload(payload: bytes) -> List[bytes]:
  """Split a logical payload into the USB packets that carry it, in sending order.

  Raises:
    ValueError: if the payload is empty or longer than 65535 bytes.
  """
  if not payload or len(payload) > 0xFFFF:
    raise ValueError(f"USB payload must be 1-65535 bytes, got {len(payload)}")
  if len(payload) <= USB_CHUNK_SIZE:
    return [bytes([0x00, FTDI_HEADER, USB_SINGLE, 0x00, 0x00, 0x00, len(payload)]) + payload]
  packets = [
    bytes([0x00, FTDI_HEADER, USB_FIRST, 0x00, 0x00])
    + len(payload).to_bytes(2, "big")
    + payload[:USB_CHUNK_SIZE]
  ]
  for sequence, offset in enumerate(range(USB_CHUNK_SIZE, len(payload), USB_CHUNK_SIZE), 1):
    header = bytes([0x00, FTDI_HEADER, USB_CONTINUATION, 0x00, 0x00, 0x00, sequence & 0xFF])
    packets.append(header + payload[offset : offset + USB_CHUNK_SIZE])
  return packets


def usb_packet_type(packet: bytes) -> int:
  """Return a USB packet's type byte.

  Raises:
    FrameError: if the packet is shorter than its header or does not start with 0x00 0x0C.
  """
  if len(packet) < 3 or packet[0] != 0x00 or packet[1] != FTDI_HEADER:
    raise FrameError(f"Not a CLARIOstar USB packet: {packet.hex()}")
  return packet[2]


def usb_total_length(first_packet: bytes) -> int:
  """Return the total payload length announced by a type-0x02 packet."""
  if len(first_packet) < USB_HEADER_SIZE:
    raise FrameError(f"USB first packet too short: {first_packet.hex()}")
  return int.from_bytes(first_packet[5:7], "big")
