"""CLARIOstar wire framing for both interfaces.

Both interfaces carry the same logical payloads (command family, command, parameters); only
the framing around them differs.

FTDI (VID 0x0403 / PID 0xBB68): BMG's FTDI frame (`pylabrobot.bmg_labtech.protocol.framing`)
with the CLARIOstar's header byte, 0x0C.

USB (VID 0x0483 / PID 0xA29A), 64-byte packets, each with a 7-byte header::

  0x00 0x0C | type (1) | 3 bytes | payload (up to 57)

  type 0x01: a whole message; byte 6 is the payload length.
  type 0x02: first packet of a longer message; bytes 5-6 are the total payload length (BE).
  type 0x03: a continuation packet; byte 6 is its sequence number, counting from 1.
  type 0x04: an acknowledgement, sent before the continuation packets of a long message.
"""

from typing import List

from pylabrobot.bmg_labtech.protocol.errors import FrameError

# The CLARIOstar's header byte, in its FTDI frames and its USB packets.
FTDI_HEADER = 0x0C

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
