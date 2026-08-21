import struct
import unittest

from pylabrobot.agilent.bravo.protocol import agile_packet
from pylabrobot.agilent.bravo.protocol.agile_7612_crc import crc8_maxim
from pylabrobot.agilent.bravo.protocol.agile_7612_packet import (
  AGILE_PACKET_SIZE,
  AgileReply,
  _make_packet,
  crc8,
  get_group_a_status,
  move_absolute_value,
  move_go,
  move_jog_value,
  move_relative_value,
  register_get,
  register_set_value,
  reset_faults,
  servo_disable,
  servo_enable,
  verify_packet,
)


class Agile7612ChecksumSelectionTests(unittest.TestCase):
  def test_module_crc8_is_the_maxim_function(self):
    # crc8 is a plain alias (crc8 = crc8_maxim); if it is repointed at the
    # legacy SMBUS crc8, every builder in this module keeps building
    # ten-byte packets that pass their own verify_packet check while
    # carrying the wrong checksum family for real Agile 7612 hardware.
    self.assertIs(crc8, crc8_maxim)

  def test_uses_maxim_not_smbus(self):
    # Same nine body bytes as the legacy (SMBUS) builder, different CRC byte.
    agile_7612_pkt = register_get(0, 0x0100)
    std_pkt = agile_packet.register_get(0, 0x0100)
    self.assertEqual(agile_7612_pkt[:9], std_pkt[:9])
    self.assertNotEqual(agile_7612_pkt[9], std_pkt[9])

  def test_verify_packet_rejects_smbus_checksummed_packet(self):
    # A packet checksummed with the legacy SMBUS CRC must not verify against
    # this module's MAXIM verify_packet.
    std_pkt = agile_packet.register_get(0, 0x0100)
    self.assertFalse(verify_packet(std_pkt))


class Agile7612PacketBuilderTests(unittest.TestCase):
  def test_register_get(self):
    pkt = register_get(0, 0x0100)
    self.assertEqual(len(pkt), AGILE_PACKET_SIZE)
    self.assertTrue(verify_packet(pkt))
    self.assertEqual(pkt, bytes.fromhex("01000001000000000093"))

  def test_register_set_value(self):
    pkt = register_set_value(0, 0x0200, 0x12345678)
    self.assertTrue(verify_packet(pkt))
    self.assertEqual(pkt, bytes.fromhex("02000002785634120080"))

  def test_move_go(self):
    pkt = move_go(0, 0x0F)
    self.assertTrue(verify_packet(pkt))
    self.assertEqual(pkt, bytes.fromhex("13000f0000000000000d"))

  def test_servo_enable(self):
    pkt = servo_enable(0, 0)
    self.assertTrue(verify_packet(pkt))
    self.assertEqual(pkt, bytes.fromhex("2000000000000000006d"))

  def test_servo_disable(self):
    pkt = servo_disable(0, 0)
    self.assertTrue(verify_packet(pkt))
    self.assertEqual(pkt, bytes.fromhex("210000000000000000c9"))

  def test_reset_faults(self):
    pkt = reset_faults(0, 0x0F)
    self.assertTrue(verify_packet(pkt))
    self.assertEqual(pkt, bytes.fromhex("30000f00000000000095"))

  def test_get_group_a_status(self):
    pkt = get_group_a_status(0)
    self.assertTrue(verify_packet(pkt))
    self.assertEqual(pkt, bytes.fromhex("400000000000000000da"))

  def test_corrupted_crc_is_rejected(self):
    pkt = bytearray(register_get(0, 0x0100))
    pkt[9] ^= 0xFF
    self.assertFalse(verify_packet(bytes(pkt)))

  def test_wrong_length_is_rejected(self):
    self.assertFalse(verify_packet(b"\x00" * 9))
    self.assertFalse(verify_packet(b"\x00" * 11))


class Agile7612MotionBuilderByteVectorTests(unittest.TestCase):
  """Pins the exact byte layout of the builders that drive the gantry.

  Each payload is ``<Bf2x``: axis byte, then the value as a little-endian
  float32, then two pad bytes. Transposing the axis and value fields (e.g.
  packing as ``<fB2x``) changes every byte from the axis position onward,
  which these literal vectors catch and ``verify_packet`` alone cannot,
  since a transposed-but-internally-consistent packet still checksums valid.
  """

  def test_move_absolute_value(self):
    pkt = move_absolute_value(controller_id=0, axis=2, position_ticks=1000.0)
    self.assertEqual(pkt, bytes.fromhex("10000200007a440000dc"))

  def test_move_relative_value(self):
    pkt = move_relative_value(controller_id=3, axis=0, delta_ticks=-1574.8)
    self.assertEqual(pkt, bytes.fromhex("1103009ad9c4c40000f2"))

  def test_move_jog_value(self):
    pkt = move_jog_value(controller_id=5, axis=4, velocity=12.5)
    self.assertEqual(pkt, bytes.fromhex("1205040000484100002f"))


class Agile7612ReplyTests(unittest.TestCase):
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
    pkt = register_set_value(0, 0x0200, 0x12345678)
    reply = AgileReply.from_packet(pkt)
    self.assertEqual(reply.get_register_value(), 0x12345678)

  def test_get_float_value_roundtrip(self):
    payload = struct.pack("<H", 0x0200) + struct.pack("<f", 3.5)
    pkt = _make_packet(0x04, 0, payload)
    reply = AgileReply.from_packet(pkt)
    self.assertAlmostEqual(reply.get_float_value(), 3.5, places=3)


if __name__ == "__main__":
  unittest.main()
