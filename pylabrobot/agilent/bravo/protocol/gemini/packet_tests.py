import unittest

from pylabrobot.agilent.bravo.protocol.gemini.enums import CommandTypes, CommonSubCommands
from pylabrobot.agilent.bravo.protocol.gemini.packet import (
  BROADCAST_ADDRESS,
  HOST_ADDRESS,
  MASTER_ADDRESS,
  InstructionAddress,
  Packet,
)


class InstructionAddressTests(unittest.TestCase):
  def test_byte_encoding(self):
    cases = [
      (0, 0, 0x00),  # host
      (1, 0, 0x01),  # master
      (63, 0, 0x3F),  # broadcast
      (4, 0, 0x04),
      (4, 1, 0x44),
      (5, 1, 0x45),
      (6, 0, 0x06),
      (6, 1, 0x46),
    ]
    for node_id, dev_id, expected_byte in cases:
      addr = InstructionAddress(node_id=node_id, dev_id=dev_id)
      self.assertEqual(addr.byte, expected_byte)
      roundtrip = InstructionAddress.from_byte(expected_byte)
      self.assertEqual(roundtrip.node_id, node_id)
      self.assertEqual(roundtrip.dev_id, dev_id)

  def test_well_known_addresses(self):
    self.assertEqual(HOST_ADDRESS.byte, 0x00)
    self.assertEqual(MASTER_ADDRESS.byte, 0x01)
    self.assertEqual(BROADCAST_ADDRESS.byte, 0x3F)

  def test_node_id_out_of_range_raises(self):
    for node_id in (-1, 64, 100):
      with self.assertRaises(ValueError):
        InstructionAddress(node_id=node_id, dev_id=0)

  def test_dev_id_out_of_range_raises(self):
    for dev_id in (-1, 4, 10):
      with self.assertRaises(ValueError):
        InstructionAddress(node_id=0, dev_id=dev_id)


class PacketConstructionTests(unittest.TestCase):
  def test_get_request_encoding(self):
    p = Packet.get_request(
      dest=InstructionAddress(node_id=4, dev_id=0),
      sub_command=CommonSubCommands.FW_VERSION,
      msg_id=0,
    )
    self.assertEqual(p.to_bytes(), bytes.fromhex("0004030400000000"))

  def test_msg_id_encoded_in_high_nibble(self):
    p = Packet(
      src=HOST_ADDRESS,
      dest=InstructionAddress(4),
      cmd_type=CommandTypes.GETCMD,
      sub_command=4,
      msg_id=2,
    )
    # byte 2 = (msg_id=2 << 4) | (cmd_type=3) = 0x23
    self.assertEqual(p.to_bytes()[2], 0x23)

  def test_roundtrip_simple(self):
    original = Packet(
      src=InstructionAddress(4, 1),
      dest=HOST_ADDRESS,
      cmd_type=CommandTypes.GETCMD_RESP,
      sub_command=30,
      cmd_val=0x12345678,
      msg_id=1,
    )
    recovered = Packet.from_bytes(original.to_bytes())
    self.assertEqual(recovered, original)

  def test_cmd_val_is_big_endian(self):
    p = Packet(
      src=HOST_ADDRESS,
      dest=InstructionAddress(1),
      cmd_type=CommandTypes.SETCMD,
      sub_command=0,
      cmd_val=0x01020304,
    )
    b = p.to_bytes()
    self.assertEqual(b[4:8], b"\x01\x02\x03\x04")

  def test_response_predicates(self):
    resp = Packet(HOST_ADDRESS, HOST_ADDRESS, CommandTypes.GETCMD_RESP, 4, 57)
    self.assertTrue(resp.is_response())
    self.assertFalse(resp.is_error())

    err = Packet(HOST_ADDRESS, HOST_ADDRESS, CommandTypes.SETCMD_ERR_RESP, 4, 3)
    self.assertTrue(err.is_response())
    self.assertTrue(err.is_error())

    req = Packet(HOST_ADDRESS, HOST_ADDRESS, CommandTypes.SETCMD, 4, 0)
    self.assertFalse(req.is_response())

  def test_rejects_wrong_length(self):
    with self.assertRaises(ValueError):
      Packet.from_bytes(b"\x00" * 7)
    with self.assertRaises(ValueError):
      Packet.from_bytes(b"\x00" * 9)

  def test_reserved_bits_are_not_preserved(self):
    # Bits 6-7 of byte 2 are reserved and are dropped on decode: a packet
    # carrying them cannot re-serialize to its input bytes.
    with_reserved = bytes([0x01, 0x02, 0b1100_0011, 0x04, 0, 0, 0, 5])
    decoded = Packet.from_bytes(with_reserved)
    self.assertEqual(decoded.to_bytes(), bytes([0x01, 0x02, 0b0000_0011, 0x04, 0, 0, 0, 5]))
    self.assertEqual(decoded.cmd_type, 0x03)
    self.assertEqual(decoded.msg_id, 0)

  def test_out_of_range_fields_raise(self):
    with self.assertRaises(ValueError):
      Packet(HOST_ADDRESS, HOST_ADDRESS, cmd_type=0x10, sub_command=0)
    with self.assertRaises(ValueError):
      Packet(HOST_ADDRESS, HOST_ADDRESS, cmd_type=0, sub_command=0, msg_id=4)
    with self.assertRaises(ValueError):
      Packet(HOST_ADDRESS, HOST_ADDRESS, cmd_type=0, sub_command=0x100)
    with self.assertRaises(ValueError):
      Packet(HOST_ADDRESS, HOST_ADDRESS, cmd_type=0, sub_command=0, cmd_val=1 << 32)


if __name__ == "__main__":
  unittest.main()
