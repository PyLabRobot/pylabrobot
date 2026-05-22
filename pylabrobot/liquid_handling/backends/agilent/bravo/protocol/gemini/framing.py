"""Gemini outer TCP frame header + multipacket/serial payload wrappers.

Frame layout (from ``GeminiAPI.Communication.Core.TCPConnection``)::

    bytes 0-1   msg_sync           0xAAAA (little-endian)
    bytes 2-3   protocol_version   0x0001 (little-endian)
    bytes 4-5   payload_type       little-endian uint16; see TCPMessageType
    bytes 6-7   payload_size       little-endian uint16; bytes of payload following

    bytes 8..   payload            payload_size bytes, interpretation per type

Payload types:
    1  PACKET        exactly 8 bytes — one :class:`Packet`
    4  MULTIPACKET   up to 512 bytes — outgoing: N×8 concatenated packets;
                                       incoming: MultipacketResponse (8 bytes)
    5  SERIAL_DATA   exactly 9 bytes — serial-peripheral payload
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
  FRAME_HEADER_SIZE,
  MAX_MULTIPACKET_SIZE,
  MAX_PACKETS_PER_MULTIPACKET,
  MSG_SYNC,
  PACKET_SIZE,
  PROTOCOL_VERSION,
  TCPMessageType,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import Packet

_HEADER_FMT = "<HHHH"


@dataclass(slots=True)
class FrameHeader:
  msg_sync: int
  protocol_version: int
  payload_type: int
  payload_size: int

  def to_bytes(self) -> bytes:
    return struct.pack(
      _HEADER_FMT,
      self.msg_sync,
      self.protocol_version,
      self.payload_type,
      self.payload_size,
    )

  @classmethod
  def from_bytes(cls, data: bytes) -> "FrameHeader":
    if len(data) < FRAME_HEADER_SIZE:
      raise ValueError(f"Frame header requires {FRAME_HEADER_SIZE} bytes, got {len(data)}")
    sync, ver, ptype, psize = struct.unpack_from(_HEADER_FMT, data, 0)
    return cls(msg_sync=sync, protocol_version=ver, payload_type=ptype, payload_size=psize)

  @property
  def is_valid_sync(self) -> bool:
    return self.msg_sync == MSG_SYNC


# --- Multipacket batch (outgoing) --------------------------------------------


def pack_multipacket_batch(packets: list[Packet]) -> bytes:
  """Serialize a list of Packets as a single multipacket payload.

  Raises ``ValueError`` if the batch would exceed the wire limit.
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
  """Parse N concatenated 8-byte packets from an outgoing multipacket payload."""
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


@dataclass(slots=True)
class MultipacketResponse:
  """Reply to a multipacket batch (TCPMessageType.MULTIPACKET, 8 bytes).

  From ``TCPMultipacketResponsePayload``::

      u16 num_exchanges       count of packets accepted
      u16 error_code          0 = success; non-zero = NAK (see device_error_nak)
      u8  error_device_addr   address of device that NAK'd (if error)
      u8  device_error_nak    :class:`CommandNAKTypes` value
      u16 padding             reserved, should be 0
  """

  num_exchanges: int
  error_code: int
  error_device_addr: int
  device_error_nak: int
  padding: int = 0

  @property
  def is_success(self) -> bool:
    return self.error_code == 0

  def to_bytes(self) -> bytes:
    return struct.pack(
      _MP_RESPONSE_FMT,
      self.num_exchanges,
      self.error_code,
      self.error_device_addr,
      self.device_error_nak,
      self.padding,
    )

  @classmethod
  def from_bytes(cls, data: bytes) -> "MultipacketResponse":
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
  """Wrap a single 8-byte Packet in a TCPMessageType.PACKET frame."""
  payload = packet.to_bytes()
  header = FrameHeader(
    msg_sync=MSG_SYNC,
    protocol_version=PROTOCOL_VERSION,
    payload_type=TCPMessageType.PACKET,
    payload_size=len(payload),
  )
  return header.to_bytes() + payload


def pack_multipacket_frame(packets: list[Packet]) -> bytes:
  """Wrap a packet batch in a TCPMessageType.MULTIPACKET frame."""
  payload = pack_multipacket_batch(packets)
  header = FrameHeader(
    msg_sync=MSG_SYNC,
    protocol_version=PROTOCOL_VERSION,
    payload_type=TCPMessageType.MULTIPACKET,
    payload_size=len(payload),
  )
  return header.to_bytes() + payload


def pack_serial_frame(payload: bytes) -> bytes:
  """Wrap 9-byte serial-device data in a TCPMessageType.SERIAL_DATA frame."""
  if len(payload) != 9:
    raise ValueError(f"serial payload must be 9 bytes, got {len(payload)}")
  header = FrameHeader(
    msg_sync=MSG_SYNC,
    protocol_version=PROTOCOL_VERSION,
    payload_type=TCPMessageType.SERIAL_DATA,
    payload_size=len(payload),
  )
  return header.to_bytes() + payload
