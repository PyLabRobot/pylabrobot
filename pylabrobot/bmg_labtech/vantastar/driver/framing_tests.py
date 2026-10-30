"""VANTAstar USB framing tests.

These check the framing layer's own invariants (packet headers, splitting, reassembly round
trip).  They do not assert that a real VANTAstar accepts these packets; that needs a USB
capture.
"""

import unittest

from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.bmg_labtech.vantastar.driver.framing import (
  ACKNOWLEDGEMENT,
  CHUNK_SIZE,
  CONTINUATION,
  DEVICE_ID,
  FIRST,
  HEADER_SIZE,
  HOST_ACKNOWLEDGEMENT,
  MAXIMUM_PAYLOAD,
  SINGLE,
  get_packet_type,
  get_packet_value,
  is_acceptance,
  wrap_payload,
)


class WrapPayloadTests(unittest.TestCase):
  """Splitting payloads into VANTAstar packets."""

  def test_device_id_is_vantastar(self):
    """The VANTAstar's device id is 0x16."""
    self.assertEqual(DEVICE_ID, 0x16)

  def test_short_payload_is_one_single_packet(self):
    """A 1-byte payload is one single packet whose value is the payload length."""
    (packet,) = wrap_payload(b"\x80")
    self.assertEqual(packet, bytes.fromhex("0016010000000180"))
    self.assertEqual(get_packet_type(packet), SINGLE)
    self.assertEqual(get_packet_value(packet), 1)

  def test_57_bytes_fill_one_packet(self):
    """A 57-byte payload fills one 64-byte single packet."""
    (packet,) = wrap_payload(bytes(range(CHUNK_SIZE)))
    self.assertEqual(len(packet), 64)
    self.assertEqual(packet[:HEADER_SIZE], bytes.fromhex("00160100000039"))

  def test_58_bytes_split_into_first_and_continuation(self):
    """A 58-byte payload splits into a first packet carrying the total length and continuation
    packet 1."""
    payload = bytes(i & 0xFF for i in range(CHUNK_SIZE + 1))
    packets = wrap_payload(payload)
    self.assertEqual([len(p) for p in packets], [64, 8])
    self.assertEqual(packets[0][:HEADER_SIZE], bytes.fromhex("0016020000003a"))
    self.assertEqual(packets[1][:HEADER_SIZE], bytes.fromhex("00160300000001"))
    self.assertEqual(get_packet_type(packets[0]), FIRST)
    self.assertEqual(get_packet_value(packets[0]), 58)
    self.assertEqual(get_packet_type(packets[1]), CONTINUATION)
    self.assertEqual(get_packet_value(packets[1]), 1)

  def test_long_payload_round_trips(self):
    """A long payload's continuations are numbered from 1, and the packets' chunks join back into
    the payload."""
    payload = bytes(i & 0xFF for i in range(200))
    packets = wrap_payload(payload)
    self.assertEqual(get_packet_type(packets[0]), FIRST)
    for seq, packet in enumerate(packets[1:], 1):
      self.assertEqual(get_packet_type(packet), CONTINUATION)
      self.assertEqual(get_packet_value(packet), seq)
    reassembled = b"".join(p[HEADER_SIZE:] for p in packets)
    self.assertEqual(reassembled, payload)
    self.assertEqual(get_packet_value(packets[0]), len(payload))

  def test_empty_payload_rejected(self):
    """An empty payload is refused."""
    with self.assertRaises(ValueError):
      wrap_payload(b"")

  def test_oversized_payload_rejected(self):
    """A payload above the maximum payload size is refused."""
    with self.assertRaises(ValueError):
      wrap_payload(bytes(MAXIMUM_PAYLOAD + 1))


class PacketInspectionTests(unittest.TestCase):
  """Reading packet headers and telling acknowledgements apart."""

  def test_packet_type_requires_vantastar_header(self):
    """A packet's type is read from a VANTAstar header; another device id raises FrameError."""
    self.assertEqual(get_packet_type(bytes.fromhex("0016010000000180")), SINGLE)
    # A CLARIOstar packet (header byte 0x0C) is not a VANTAstar packet.
    with self.assertRaises(FrameError):
      get_packet_type(bytes.fromhex("000c010000000180"))

  def test_packet_value_requires_full_header(self):
    """A packet shorter than its header raises FrameError."""
    with self.assertRaises(FrameError):
      get_packet_value(bytes.fromhex("001601"))

  def test_host_acknowledgement_is_an_acceptance(self):
    """The host's acknowledgement is an acceptance."""
    self.assertEqual(HOST_ACKNOWLEDGEMENT, bytes([0x00, DEVICE_ID, ACKNOWLEDGEMENT, 0x01]))
    self.assertTrue(is_acceptance(HOST_ACKNOWLEDGEMENT))

  def test_refusing_acknowledgement_is_not_an_acceptance(self):
    """An acknowledgement ending in 0x00 is a refusal, not an acceptance."""
    refusal = bytes([0x00, DEVICE_ID, ACKNOWLEDGEMENT, 0x00])
    self.assertFalse(is_acceptance(refusal))

  def test_non_acknowledgement_is_not_an_acceptance(self):
    """A data packet is not an acceptance."""
    (single,) = wrap_payload(b"\x80")
    self.assertFalse(is_acceptance(single))


if __name__ == "__main__":
  unittest.main()
