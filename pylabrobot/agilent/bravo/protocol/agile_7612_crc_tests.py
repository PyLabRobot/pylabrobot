import unittest

from pylabrobot.agilent.bravo.protocol.agile_7612_crc import crc8_maxim
from pylabrobot.agilent.bravo.protocol.agile_packet import crc8 as crc8_smbus


class Crc8MaximKnownAnswerTests(unittest.TestCase):
  def test_controller_identification_packet(self):
    # Captured Agile 7612 controller-identification packet (register 0x90);
    # the expected byte is the packet's own trailing CRC byte, computed
    # independently of this module's crc8_maxim().
    pkt = bytes.fromhex("0990000000000000003f")
    self.assertEqual(crc8_maxim(pkt[:9], 9), 0x3F)
    self.assertEqual(pkt[9], 0x3F)

  def test_jog_trigger_packet(self):
    # Captured Agile 7612 jog-trigger packet (header 0x80, byte[7]=0x36);
    # the expected byte is the packet's own trailing CRC byte.
    pkt = bytes.fromhex("800040000000053600d8")
    self.assertEqual(crc8_maxim(pkt[:9], 9), 0xD8)
    self.assertEqual(pkt[9], 0xD8)

  def test_matches_published_check_value(self):
    # CRC-8/MAXIM's published catalogue check value: CRC(b"123456789") == 0xA1.
    self.assertEqual(crc8_maxim(b"123456789"), 0xA1)

  def test_empty_input(self):
    self.assertEqual(crc8_maxim(b"", 0), 0x00)


class Crc8MaximDiffersFromSmbusTests(unittest.TestCase):
  def test_differs_on_shared_data(self):
    data = b"\x01\x00\x00\x01\x00\x00\x00\x00\x00"
    self.assertNotEqual(crc8_maxim(data), crc8_smbus(data))


if __name__ == "__main__":
  unittest.main()
