import struct
import unittest

from pylabrobot.agilent.bravo.protocol.gemini.enums import (
  MAX_MULTIPACKET_SIZE,
  MAX_PACKETS_PER_MULTIPACKET,
  MSG_SYNC,
  PROTOCOL_VERSION,
  CommandTypes,
  CommonSubCommands,
  TCPMessageType,
)
from pylabrobot.agilent.bravo.protocol.gemini.framing import (
  FrameHeader,
  MultipacketResponse,
  pack_multipacket_batch,
  pack_multipacket_frame,
  pack_packet_frame,
  pack_serial_frame,
  unpack_multipacket_batch,
)
from pylabrobot.agilent.bravo.protocol.gemini.packet import HOST_ADDRESS, InstructionAddress, Packet


class FrameHeaderTests(unittest.TestCase):
  def test_packs_to_eight_little_endian_bytes(self):
    h = FrameHeader(
      msg_sync=MSG_SYNC,
      protocol_version=PROTOCOL_VERSION,
      payload_type=TCPMessageType.PACKET,
      payload_size=8,
    )
    self.assertEqual(h.to_bytes(), bytes.fromhex("aaaa010001000800"))

  def test_roundtrip(self):
    h = FrameHeader(MSG_SYNC, PROTOCOL_VERSION, TCPMessageType.MULTIPACKET, 56)
    recovered = FrameHeader.from_bytes(h.to_bytes())
    self.assertEqual(recovered, h)
    self.assertTrue(recovered.is_valid_sync)

  def test_rejects_truncated_header(self):
    with self.assertRaises(ValueError):
      FrameHeader.from_bytes(b"\x00" * 7)

  def test_rejects_bad_sync_word(self):
    bad = struct.pack("<HHHH", 0x1234, PROTOCOL_VERSION, TCPMessageType.PACKET, 0)
    header = FrameHeader.from_bytes(bad)
    self.assertFalse(header.is_valid_sync)


class FramePackHelperTests(unittest.TestCase):
  def test_pack_packet_frame_wraps_eight_byte_packet(self):
    p = Packet.get_request(InstructionAddress(4), CommonSubCommands.FW_VERSION)
    frame = pack_packet_frame(p)
    self.assertEqual(frame, bytes.fromhex("aaaa010001000800") + p.to_bytes())
    self.assertEqual(len(frame), 16)

  def test_pack_multipacket_frame_wraps_n_packets(self):
    packets = [
      Packet(HOST_ADDRESS, InstructionAddress(5), CommandTypes.SETCMD, 20, 1),
      Packet(HOST_ADDRESS, InstructionAddress(5), CommandTypes.SETCMD, 21, 0xFFFFFF00),
    ]
    frame = pack_multipacket_frame(packets)
    self.assertEqual(len(frame), 24)
    header = FrameHeader.from_bytes(frame[:8])
    self.assertEqual(header.payload_type, TCPMessageType.MULTIPACKET)
    self.assertEqual(header.payload_size, 16)

  def test_pack_serial_frame_requires_nine_byte_payload(self):
    valid = bytes(range(9))
    frame = pack_serial_frame(valid)
    header = FrameHeader.from_bytes(frame[:8])
    self.assertEqual(header.payload_type, TCPMessageType.SERIAL_DATA)
    self.assertEqual(header.payload_size, 9)

    with self.assertRaises(ValueError):
      pack_serial_frame(bytes(range(8)))
    with self.assertRaises(ValueError):
      pack_serial_frame(bytes(range(10)))


class MultipacketBatchTests(unittest.TestCase):
  def test_roundtrip(self):
    packets = [
      Packet(HOST_ADDRESS, InstructionAddress(5), CommandTypes.SETCMD, sc, i)
      for i, sc in enumerate([20, 21, 21, 21, 21, 22, 23])
    ]
    payload = pack_multipacket_batch(packets)
    recovered = unpack_multipacket_batch(payload)
    self.assertEqual(recovered, packets)

  def test_rejects_oversize_batch(self):
    packets = [
      Packet(HOST_ADDRESS, HOST_ADDRESS, CommandTypes.SETCMD, 0, 0)
      for _ in range(MAX_PACKETS_PER_MULTIPACKET + 1)
    ]
    with self.assertRaises(ValueError):
      pack_multipacket_batch(packets)

  def test_respects_byte_limit_at_packet_limit(self):
    packets = [
      Packet(HOST_ADDRESS, HOST_ADDRESS, CommandTypes.SETCMD, 0, 0)
      for _ in range(MAX_PACKETS_PER_MULTIPACKET)
    ]
    payload = pack_multipacket_batch(packets)
    self.assertLessEqual(len(payload), MAX_MULTIPACKET_SIZE)

  def test_unpack_rejects_truncated_payload(self):
    # 15 bytes is not a whole number of 8-byte packets.
    with self.assertRaises(ValueError):
      unpack_multipacket_batch(b"\x00" * 15)


class MultipacketResponseTests(unittest.TestCase):
  def test_success_roundtrip(self):
    r = MultipacketResponse(num_exchanges=7, error_code=0, error_device_addr=0, device_error_nak=0)
    data = r.to_bytes()
    self.assertEqual(data, bytes.fromhex("0700000000000000"))
    self.assertTrue(r.is_success)
    self.assertEqual(MultipacketResponse.from_bytes(data), r)

  def test_nak_encoding(self):
    # padding is nonzero here so the test can tell device_error_nak's 1-byte
    # width apart from padding's 2-byte width: with padding=0 and a
    # single-byte nak value, a <BBH>/<BHB> field-width swap between them
    # produces identical bytes and the test would not catch it.
    r = MultipacketResponse(
      num_exchanges=3, error_code=1, error_device_addr=4, device_error_nak=11, padding=9
    )
    expected = bytes.fromhex("03000100040b0900")
    self.assertEqual(r.to_bytes(), expected)
    self.assertFalse(r.is_success)

  def test_from_bytes_rejects_truncated_payload(self):
    with self.assertRaises(ValueError):
      MultipacketResponse.from_bytes(b"\x00" * 7)


if __name__ == "__main__":
  unittest.main()
