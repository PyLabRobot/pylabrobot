"""VANTAstar USB framing: BMG's packets (`pylabrobot.bmg_labtech.protocol.framing`) with the
VANTAstar's device id, 0x16, as their header byte.

The VANTAstar talks native USB (VID 0x0483 / PID 0xA29A, bulk OUT 0x01, bulk IN 0x81). Its packet
header's bytes 3-6 are one value (BE): a message's length, or a continuation's sequence number. An
acknowledgement's first data byte is 0x01 to accept the continuation packets of a long message,
0x00 to refuse them.
"""

from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.bmg_labtech.protocol.framing import USB_ACK, USB_HEADER_SIZE, usb_packet_type

DEVICE_ID = 0x16

# The host's acknowledgement of a long reply, accepting its continuation packets.
HOST_ACKNOWLEDGEMENT = bytes([0x00, DEVICE_ID, USB_ACK, 0x01])


def get_packet_value(packet: bytes) -> int:
  """Return a packet's header value: a message length, or a continuation's sequence number.

  Raises:
    FrameError: if the packet is shorter than its header.
  """
  if len(packet) < USB_HEADER_SIZE:
    raise FrameError(f"packet shorter than its header: {packet.hex()}")
  return int.from_bytes(packet[3:USB_HEADER_SIZE], "big")


def is_acceptance(packet: bytes) -> bool:
  """Whether an acknowledgement packet accepts the continuation packets that follow it."""
  packet_type = usb_packet_type(packet, header=DEVICE_ID)
  return packet_type == USB_ACK and len(packet) > 3 and bool(packet[3] & 0x01)
