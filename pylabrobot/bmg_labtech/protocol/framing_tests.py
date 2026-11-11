"""Framing tests; the FTDI ones against frames recorded from a CLARIOstar."""

import unittest

from pylabrobot.bmg_labtech.protocol.errors import ChecksumError, FrameError
from pylabrobot.bmg_labtech.protocol.framing import (
  ftdi_extract_payload,
  ftdi_validate_frame,
  ftdi_wrap_payload,
  usb_packet_type,
  usb_total_length,
  usb_wrap_payload,
)

CLARIOSTAR_HEADER = 0x0C
STATUS_QUERY = bytes.fromhex("0200090c800000970d")
STATUS_IDLE = bytes.fromhex("0200180c010506240000000000000000000000c00001160d")
INIT = bytes.fromhex("02000d0c010000100300002f0d")
RECORDED_FRAMES = [STATUS_QUERY, STATUS_IDLE, INIT]


class FTDIFramingTests(unittest.TestCase):
  def test_wrap_reproduces_every_recorded_sent_frame(self):
    for sent in (STATUS_QUERY, INIT):
      self.assertEqual(
        ftdi_wrap_payload(ftdi_extract_payload(sent), header=CLARIOSTAR_HEADER), sent
      )

  def test_every_recorded_frame_validates_with_its_header(self):
    for frame in RECORDED_FRAMES:
      ftdi_validate_frame(frame, header=CLARIOSTAR_HEADER)
      self.assertEqual(len(ftdi_extract_payload(frame)), len(frame) - 8)

  def test_another_header_byte_fails(self):
    for frame in RECORDED_FRAMES:
      with self.assertRaises(FrameError):
        ftdi_validate_frame(frame, header=0x08)

  def test_the_header_changes_only_byte_3_and_the_checksum(self):
    framed = ftdi_wrap_payload(ftdi_extract_payload(STATUS_QUERY), header=0x08)
    ftdi_validate_frame(framed, header=0x08)
    differing = [i for i, (a, b) in enumerate(zip(framed, STATUS_QUERY)) if a != b]
    self.assertEqual(differing, [3, 7])

  def test_corrupted_byte_fails_checksum(self):
    # Not a reader's reply: the recorded frame with one bit flipped.
    frame = bytearray(STATUS_IDLE)
    frame[6] ^= 0x01
    with self.assertRaises(ChecksumError):
      ftdi_validate_frame(bytes(frame), header=CLARIOSTAR_HEADER)

  def test_truncated_frame_fails(self):
    # Not a reader's reply: the recorded frame cut short.
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_IDLE[:-1], header=CLARIOSTAR_HEADER)
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_IDLE[:5], header=CLARIOSTAR_HEADER)


class USBFramingTests(unittest.TestCase):
  def test_short_payload_is_one_packet(self):
    self.assertEqual(
      usb_wrap_payload(b"\x80", header=CLARIOSTAR_HEADER), [bytes.fromhex("000c010000000180")]
    )

  def test_57_bytes_fill_one_packet(self):
    (packet,) = usb_wrap_payload(bytes(range(57)), header=CLARIOSTAR_HEADER)
    self.assertEqual(len(packet), 64)
    self.assertEqual(packet[:7], bytes.fromhex("000c0100000039"))

  def test_long_payload_splits_into_first_and_continuations(self):
    payload = bytes(i & 0xFF for i in range(130))
    packets = usb_wrap_payload(payload, header=CLARIOSTAR_HEADER)
    self.assertEqual([len(p) for p in packets], [64, 64, 23])
    self.assertEqual(packets[0][:7], bytes.fromhex("000c0200000082"))
    self.assertEqual(packets[1][:7], bytes.fromhex("000c0300000001"))
    self.assertEqual(packets[2][:7], bytes.fromhex("000c0300000002"))
    self.assertEqual(b"".join(p[7:] for p in packets), payload)
    self.assertEqual(usb_total_length(packets[0]), 130)

  def test_every_packet_carries_the_header_byte(self):
    packets = usb_wrap_payload(bytes(130), header=0x16)
    self.assertEqual(packets[0][:7], bytes.fromhex("00160200000082"))
    self.assertEqual(packets[1][:7], bytes.fromhex("00160300000001"))
    self.assertEqual(usb_packet_type(packets[0], header=0x16), 0x02)

  def test_empty_payload_rejected(self):
    with self.assertRaises(ValueError):
      usb_wrap_payload(b"", header=CLARIOSTAR_HEADER)

  def test_packet_type_requires_header(self):
    packet = bytes.fromhex("000c010000000180")
    self.assertEqual(usb_packet_type(packet, header=CLARIOSTAR_HEADER), 0x01)
    with self.assertRaises(FrameError):
      usb_packet_type(STATUS_QUERY, header=CLARIOSTAR_HEADER)
    with self.assertRaises(FrameError):
      usb_packet_type(packet, header=0x16)

  def test_sequence_number_wraps_after_255(self):
    packets = usb_wrap_payload(bytes(57 * 258), header=CLARIOSTAR_HEADER)
    self.assertEqual(packets[255][:7], bytes.fromhex("000c03000000ff"))
    self.assertEqual(packets[256][:7], bytes.fromhex("000c0300000000"))

  def test_total_length_ignores_bytes_3_and_4(self):
    self.assertEqual(usb_total_length(bytes.fromhex("000c02ffff0082")), 130)


if __name__ == "__main__":
  unittest.main()
