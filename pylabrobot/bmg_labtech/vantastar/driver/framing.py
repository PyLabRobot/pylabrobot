"""VANTAstar USB framing.

The VANTAstar talks native USB (VID 0x0483 / PID 0xA29A, bulk OUT 0x01, bulk IN 0x81). A logical
payload, the command id and its parameters or the response id and its fields, travels in packets of
up to 64 bytes, each with a 7-byte header::

  0x00 0x16 | type (1) | value (4, BE) | data (up to 57)

  type 0x01: a whole message; the value is its length.
  type 0x02: the first packet of a longer message; the value is the message's total length.
  type 0x03: a continuation packet; the value is its sequence number, counting from 1.
  type 0x04: an acknowledgement; the data's first byte is 0x01 to accept the continuation packets
    of a long message, 0x00 to refuse them.

0x16 is the VANTAstar's device id; BMG's readers share the packet format and differ in this byte.
"""

from typing import List

from pylabrobot.bmg_labtech.protocol.errors import FrameError

DEVICE_ID = 0x16

PACKET_SIZE = 64
HEADER_SIZE = 7
CHUNK_SIZE = PACKET_SIZE - HEADER_SIZE

SINGLE = 0x01
FIRST = 0x02
CONTINUATION = 0x03
ACKNOWLEDGEMENT = 0x04

# The largest logical payload a message carries.
MAXIMUM_PAYLOAD = 0xFFFE

# The host's acknowledgement of a long reply, accepting its continuation packets.
HOST_ACKNOWLEDGEMENT = bytes([0x00, DEVICE_ID, ACKNOWLEDGEMENT, 0x01])


def _packet(packet_type: int, value: int, data: bytes) -> bytes:
  """One packet: the header with `packet_type` and `value`, then `data`."""
  return bytes([0x00, DEVICE_ID, packet_type]) + value.to_bytes(4, "big") + data


def wrap_payload(payload: bytes) -> List[bytes]:
  """Split a logical payload into the packets that carry it, in sending order.

  Raises:
    ValueError: if the payload is empty or longer than `MAXIMUM_PAYLOAD` bytes.
  """
  if not payload or len(payload) > MAXIMUM_PAYLOAD:
    raise ValueError(f"payload must be 1-{MAXIMUM_PAYLOAD} bytes, got {len(payload)}")
  if len(payload) <= CHUNK_SIZE:
    return [_packet(SINGLE, len(payload), payload)]
  packets = [_packet(FIRST, len(payload), payload[:CHUNK_SIZE])]
  for sequence, offset in enumerate(range(CHUNK_SIZE, len(payload), CHUNK_SIZE), 1):
    packets.append(_packet(CONTINUATION, sequence, payload[offset : offset + CHUNK_SIZE]))
  return packets


def get_packet_type(packet: bytes) -> int:
  """Return a packet's type byte.

  Raises:
    FrameError: if the packet is shorter than its type byte or is not addressed to the VANTAstar.
  """
  if len(packet) < 3 or packet[0] != 0x00 or packet[1] != DEVICE_ID:
    raise FrameError(f"not a VANTAstar packet: {packet.hex()}")
  return packet[2]


def get_packet_value(packet: bytes) -> int:
  """Return a packet's header value: a message length, or a continuation's sequence number.

  Raises:
    FrameError: if the packet is shorter than its header.
  """
  if len(packet) < HEADER_SIZE:
    raise FrameError(f"packet shorter than its header: {packet.hex()}")
  return int.from_bytes(packet[3:HEADER_SIZE], "big")


def is_acceptance(packet: bytes) -> bool:
  """Whether an acknowledgement packet accepts the continuation packets that follow it."""
  return get_packet_type(packet) == ACKNOWLEDGEMENT and len(packet) > 3 and bool(packet[3] & 0x01)
