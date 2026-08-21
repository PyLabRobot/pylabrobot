"""Gemini outer TCP frame header and multipacket/serial payload wrappers.

This wire format is not vendor protocol documentation. It was recovered by
observing traffic between Agilent VWorks and a Darwin-generation Bravo
controller, not from a published specification. The protocol has no
authentication or encryption: anyone with network access to the instrument's
TCP port can send it commands.

Frame layout::

    bytes 0-1   msg_sync           0xAAAA (little-endian)
    bytes 2-3   protocol_version   0x0001 (little-endian)
    bytes 4-5   payload_type       little-endian uint16; see TCPMessageType
    bytes 6-7   payload_size       little-endian uint16; bytes of payload following

    bytes 8..   payload            payload_size bytes, interpretation per type

Payload types:
    1  PACKET        exactly 8 bytes -- one :class:`~.packet.Packet`
    4  MULTIPACKET   up to 512 bytes; outgoing: N x 8 concatenated packets,
                     incoming: :class:`MultipacketResponse` (8 bytes)
    5  SERIAL_DATA   exactly 9 bytes -- a serial-peripheral payload
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .enums import (
  FRAME_HEADER_SIZE,
  MAX_MULTIPACKET_SIZE,
  MAX_PACKETS_PER_MULTIPACKET,
  MSG_SYNC,
  PACKET_SIZE,
  PROTOCOL_VERSION,
  TCPMessageType,
)
from .packet import Packet

_HEADER_FMT = "<HHHH"


@dataclass
class FrameHeader:
  """The 8-byte header that precedes every Gemini frame's payload.

  Attributes:
    msg_sync: Frame sync word; must equal :data:`~.enums.MSG_SYNC` for the
      frame to be considered valid.
    protocol_version: Wire protocol version.
    payload_type: One of :class:`~.enums.TCPMessageType`.
    payload_size: Number of payload bytes following this header.
  """

  msg_sync: int
  protocol_version: int
  payload_type: int
  payload_size: int

  def to_bytes(self) -> bytes:
    """Pack this header into its 8-byte little-endian wire encoding.

    Returns:
      The packed header bytes.
    """
    return struct.pack(
      _HEADER_FMT,
      self.msg_sync,
      self.protocol_version,
      self.payload_type,
      self.payload_size,
    )

  @classmethod
  def from_bytes(cls, data: bytes) -> FrameHeader:
    """Parse a header from its 8-byte wire encoding.

    Args:
      data: At least :data:`~.enums.FRAME_HEADER_SIZE` bytes, header first.

    Returns:
      The decoded header.

    Raises:
      ValueError: If fewer than :data:`~.enums.FRAME_HEADER_SIZE` bytes are given.
    """
    if len(data) < FRAME_HEADER_SIZE:
      raise ValueError(f"Frame header requires {FRAME_HEADER_SIZE} bytes, got {len(data)}")
    sync, ver, ptype, psize = struct.unpack_from(_HEADER_FMT, data, 0)
    return cls(msg_sync=sync, protocol_version=ver, payload_type=ptype, payload_size=psize)

  @property
  def is_valid_sync(self) -> bool:
    """Whether :attr:`msg_sync` matches the expected sync word."""
    return self.msg_sync == MSG_SYNC


# --- Multipacket batch (outgoing) --------------------------------------------


def pack_multipacket_batch(packets: list[Packet]) -> bytes:
  """Serialize a list of packets as a single outgoing multipacket payload.

  Args:
    packets: The packets to concatenate, in send order.

  Returns:
    The concatenated 8-byte packet encodings.

  Raises:
    ValueError: If the batch exceeds the packet-count or byte-size wire limit.
  """
  if len(packets) > MAX_PACKETS_PER_MULTIPACKET:
    raise ValueError(
      f"multipacket exceeds {MAX_PACKETS_PER_MULTIPACKET}-packet limit (got {len(packets)})"
    )
  buf = bytearray()
  for p in packets:
    buf.extend(p.to_bytes())
  if len(buf) > MAX_MULTIPACKET_SIZE:
    raise ValueError(
      f"multipacket payload exceeds {MAX_MULTIPACKET_SIZE}-byte limit (got {len(buf)})"
    )
  return bytes(buf)


def unpack_multipacket_batch(payload: bytes) -> list[Packet]:
  """Parse an outgoing multipacket payload back into its packets.

  Args:
    payload: A payload built by :func:`pack_multipacket_batch` (or received
      unchanged from one).

  Returns:
    The packets, in their original order.

  Raises:
    ValueError: If ``payload`` is not a whole number of 8-byte packets.
  """
  if len(payload) % PACKET_SIZE != 0:
    raise ValueError(
      f"multipacket payload length {len(payload)} is not a multiple of {PACKET_SIZE}"
    )
  return [
    Packet.from_bytes(payload[i : i + PACKET_SIZE]) for i in range(0, len(payload), PACKET_SIZE)
  ]


# --- Multipacket response (incoming) -----------------------------------------

_MP_RESPONSE_FMT = "<HHBBH"
_MP_RESPONSE_SIZE = 8


@dataclass
class MultipacketResponse:
  """Reply to a multipacket batch (``TCPMessageType.MULTIPACKET``, 8 bytes).

  Attributes:
    num_exchanges: Count of packets the controller accepted before it
      succeeded or NAK'd.
    error_code: 0 on success; non-zero identifies which packet in the batch
      was rejected.
    error_device_addr: Address byte of the device that NAK'd, if any.
    device_error_nak: The :class:`~.enums.CommandNAKTypes` code, if any.
    padding: Reserved wire bytes; always 0.
  """

  num_exchanges: int
  error_code: int
  error_device_addr: int
  device_error_nak: int
  padding: int = 0

  @property
  def is_success(self) -> bool:
    """Whether every packet in the batch was accepted."""
    return self.error_code == 0

  def to_bytes(self) -> bytes:
    """Pack this response into its 8-byte wire encoding.

    Returns:
      The packed response bytes.
    """
    return struct.pack(
      _MP_RESPONSE_FMT,
      self.num_exchanges,
      self.error_code,
      self.error_device_addr,
      self.device_error_nak,
      self.padding,
    )

  @classmethod
  def from_bytes(cls, data: bytes) -> MultipacketResponse:
    """Parse a response from its 8-byte wire encoding.

    Args:
      data: At least 8 bytes, response first.

    Returns:
      The decoded response.

    Raises:
      ValueError: If fewer than 8 bytes are given.
    """
    if len(data) < _MP_RESPONSE_SIZE:
      raise ValueError(f"MultipacketResponse requires {_MP_RESPONSE_SIZE} bytes, got {len(data)}")
    num, err, addr, nak, pad = struct.unpack_from(_MP_RESPONSE_FMT, data, 0)
    return cls(
      num_exchanges=num,
      error_code=err,
      error_device_addr=addr,
      device_error_nak=nak,
      padding=pad,
    )


# --- Frame pack helpers ------------------------------------------------------


def pack_packet_frame(packet: Packet) -> bytes:
  """Wrap a single packet in a ``TCPMessageType.PACKET`` frame.

  Args:
    packet: The packet to send.

  Returns:
    The full frame: header followed by the packet's 8 bytes.
  """
  payload = packet.to_bytes()
  header = FrameHeader(
    msg_sync=MSG_SYNC,
    protocol_version=PROTOCOL_VERSION,
    payload_type=TCPMessageType.PACKET,
    payload_size=len(payload),
  )
  return header.to_bytes() + payload


def pack_multipacket_frame(packets: list[Packet]) -> bytes:
  """Wrap a packet batch in a ``TCPMessageType.MULTIPACKET`` frame.

  Args:
    packets: The packets to send, in send order.

  Returns:
    The full frame: header followed by the concatenated packets.
  """
  payload = pack_multipacket_batch(packets)
  header = FrameHeader(
    msg_sync=MSG_SYNC,
    protocol_version=PROTOCOL_VERSION,
    payload_type=TCPMessageType.MULTIPACKET,
    payload_size=len(payload),
  )
  return header.to_bytes() + payload


def pack_serial_frame(payload: bytes) -> bytes:
  """Wrap a 9-byte serial-peripheral payload in a ``TCPMessageType.SERIAL_DATA`` frame.

  Args:
    payload: Exactly 9 bytes of serial-device data.

  Returns:
    The full frame: header followed by the 9-byte payload.

  Raises:
    ValueError: If ``payload`` is not exactly 9 bytes.
  """
  if len(payload) != 9:
    raise ValueError(f"serial payload must be 9 bytes, got {len(payload)}")
  header = FrameHeader(
    msg_sync=MSG_SYNC,
    protocol_version=PROTOCOL_VERSION,
    payload_type=TCPMessageType.SERIAL_DATA,
    payload_size=len(payload),
  )
  return header.to_bytes() + payload
