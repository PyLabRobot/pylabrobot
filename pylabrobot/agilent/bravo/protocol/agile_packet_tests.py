import unittest

from pylabrobot.agilent.bravo.protocol.agile_packet import (
  AGILE_PACKET_SIZE,
  AgileReply,
  crc8,
  move_absolute_value,
  move_go,
  move_jog_value,
  move_relative_value,
  register_get,
  servo_enable,
  verify_packet,
)


class Crc8SmbusTests(unittest.TestCase):
  def test_matches_published_check_value(self):
    # CRC-8/SMBUS's published catalogue check value: CRC(b"123456789") == 0xF4.
    self.assertEqual(crc8(b"123456789"), 0xF4)

  def test_empty_input(self):
    self.assertEqual(crc8(b"", 0), 0)

  def test_deterministic(self):
    # No independently-captured SMBUS packet trace exists to pin a
    # known-answer value beyond the catalogue check value above; this pins
    # that repeated calls on the same input agree with each other.
    data = b"\xa1\x00\x01\x02\x03\x04\x05\x06\x07"
    self.assertEqual(crc8(data), crc8(data))


class AgilePacketBuilderTests(unittest.TestCase):
  def test_register_get_packet_size_and_crc(self):
    pkt = register_get(0, 0x0100)
    self.assertEqual(len(pkt), AGILE_PACKET_SIZE)
    self.assertTrue(verify_packet(pkt))

  def test_move_go_packet(self):
    pkt = move_go(0, 0x0F)
    self.assertEqual(len(pkt), AGILE_PACKET_SIZE)
    self.assertTrue(verify_packet(pkt))

  def test_servo_enable_packet(self):
    pkt = servo_enable(0, 0)
    self.assertEqual(len(pkt), AGILE_PACKET_SIZE)
    self.assertTrue(verify_packet(pkt))

  def test_corrupted_crc_is_rejected(self):
    pkt = bytearray(register_get(0, 0x0100))
    pkt[9] ^= 0xFF
    self.assertFalse(verify_packet(bytes(pkt)))

  def test_wrong_length_is_rejected(self):
    self.assertFalse(verify_packet(b"\x00" * 9))
    self.assertFalse(verify_packet(b"\x00" * 11))


class AgileMotionBuilderByteVectorTests(unittest.TestCase):
  """Pins the exact byte layout of the builders that drive the gantry.

  Each payload is ``<Bf2x``: axis byte, then the value as a little-endian
  float32, then two pad bytes. Transposing the axis and value fields (e.g.
  packing as ``<fB2x``) changes every byte from the axis position onward,
  which these literal vectors catch and ``verify_packet`` alone cannot,
  since a transposed-but-internally-consistent packet still checksums valid.
  """

  def test_move_absolute_value(self):
    pkt = move_absolute_value(controller_id=0, axis=2, position_ticks=1000.0)
    self.assertEqual(pkt, bytes.fromhex("10000200007a440000bf"))

  def test_move_relative_value(self):
    pkt = move_relative_value(controller_id=3, axis=0, delta_ticks=-1574.8)
    self.assertEqual(pkt, bytes.fromhex("1103009ad9c4c4000064"))

  def test_move_jog_value(self):
    pkt = move_jog_value(controller_id=5, axis=4, velocity=12.5)
    self.assertEqual(pkt, bytes.fromhex("1205040000484100009b"))


class AgileReplyTests(unittest.TestCase):
  def test_from_packet(self):
    pkt = register_get(0, 0x0100)
    reply = AgileReply.from_packet(pkt)
    self.assertTrue(reply.crc_valid)
    self.assertEqual(reply.controller_id, 0)
    self.assertEqual(reply.header, pkt[0])

  def test_from_packet_rejects_wrong_length(self):
    with self.assertRaises(ValueError):
      AgileReply.from_packet(b"\x00" * 9)

  def test_get_register_value_roundtrip(self):
    from pylabrobot.agilent.bravo.protocol.agile_packet import register_set_value

    pkt = register_set_value(0, 0x0200, 0x12345678)
    reply = AgileReply.from_packet(pkt)
    self.assertEqual(reply.get_register_value(), 0x12345678)

  def test_get_float_value_roundtrip(self):
    import struct

    from pylabrobot.agilent.bravo.protocol.agile_packet import _make_packet

    payload = struct.pack("<H", 0x0200) + struct.pack("<f", 3.5)
    pkt = _make_packet(0x04, 0, payload)
    reply = AgileReply.from_packet(pkt)
    self.assertAlmostEqual(reply.get_float_value(), 3.5, places=3)


if __name__ == "__main__":
  unittest.main()
