"""Framing tests, against the frames recorded in the driver and drawer tests."""

import unittest

from pylabrobot.bmg_labtech.clariostar.driver.errors import (
  ChecksumError,
  FrameError,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  VERIFICATION_DATA_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.drawer_tests import (
  CLOSE_DRAWER_FRAME,
  CLOSE_DRAWER_REPLY,
  OPEN_DRAWER_FRAME,
  OPEN_DRAWER_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.framing import (
  ftdi_extract_payload,
  ftdi_validate_frame,
  ftdi_wrap_payload,
  usb_packet_type,
  usb_total_length,
  usb_wrap_payload,
)
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  FIRMWARE_INFO_FRAME,
  FIRMWARE_INFO_REPLY,
  INIT_FRAME,
  INIT_REPLY,
  PARAMETERS_FRAME,
  PARAMETERS_REPLY,
  REFUSED,
  STATUS_BUSY,
  STATUS_DRAWER_OPEN,
  STATUS_IDLE,
  STATUS_MOTORS_OFF,
  STATUS_NOT_INITIALIZED,
  STATUS_PLATE_DETECTED,
  STATUS_QUERY_FRAME,
  STATUS_TEMPERATURES,
  STATUS_TRUNCATED,
  STATUS_WARNING,
)

# Every recorded exchange whose reply arrived whole.
REAL_EXCHANGES = [
  (STATUS_QUERY_FRAME, reply)
  for reply in (
    STATUS_IDLE,
    STATUS_BUSY,
    STATUS_DRAWER_OPEN,
    STATUS_PLATE_DETECTED,
    STATUS_TEMPERATURES,
    STATUS_MOTORS_OFF,
    STATUS_NOT_INITIALIZED,
    STATUS_WARNING,
  )
] + [
  (FIRMWARE_INFO_FRAME, FIRMWARE_INFO_REPLY),
  (PARAMETERS_FRAME, PARAMETERS_REPLY),
  (INIT_FRAME, INIT_REPLY),
  (OPEN_DRAWER_FRAME, OPEN_DRAWER_REPLY),
  (CLOSE_DRAWER_FRAME, CLOSE_DRAWER_REPLY),
  *REFUSED.values(),
]
STATUS_REPLY_FRAME = STATUS_PLATE_DETECTED


class FTDIFramingTests(unittest.TestCase):
  def test_wrap_reproduces_every_real_sent_frame(self):
    for sent, _ in REAL_EXCHANGES:
      self.assertEqual(ftdi_wrap_payload(ftdi_extract_payload(sent)), sent)
    self.assertEqual(ftdi_wrap_payload(b"\x80"), STATUS_QUERY_FRAME)

  def test_every_real_frame_validates(self):
    for sent, received in REAL_EXCHANGES:
      ftdi_validate_frame(sent)
      ftdi_validate_frame(received)
      self.assertEqual(len(ftdi_extract_payload(received)), len(received) - 8)

  def test_real_truncated_reply_fails(self):
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_TRUNCATED)

  def test_a_checksum_past_16_bits_validates(self):
    # The 96-well verification read's data reply: its checksum is 0x01c462.
    self.assertGreater(int.from_bytes(VERIFICATION_DATA_REPLY[-4:-1], "big"), 0xFFFF)
    ftdi_validate_frame(VERIFICATION_DATA_REPLY)

  def test_corrupted_byte_fails_checksum(self):
    # Not a reader's reply: the recorded frame with one bit flipped.
    frame = bytearray(STATUS_REPLY_FRAME)
    frame[6] ^= 0x01
    with self.assertRaises(ChecksumError):
      ftdi_validate_frame(bytes(frame))

  def test_truncated_frame_fails(self):
    # Not a reader's reply: the recorded frame cut short.
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_REPLY_FRAME[:-1])
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_REPLY_FRAME[:5])


class USBFramingTests(unittest.TestCase):
  def test_short_payload_is_one_packet(self):
    self.assertEqual(usb_wrap_payload(b"\x80"), [bytes.fromhex("000c010000000180")])

  def test_57_bytes_fill_one_packet(self):
    (packet,) = usb_wrap_payload(bytes(range(57)))
    self.assertEqual(len(packet), 64)
    self.assertEqual(packet[:7], bytes.fromhex("000c0100000039"))

  def test_long_payload_splits_into_first_and_continuations(self):
    payload = bytes(i & 0xFF for i in range(130))
    packets = usb_wrap_payload(payload)
    self.assertEqual([len(p) for p in packets], [64, 64, 23])
    self.assertEqual(packets[0][:7], bytes.fromhex("000c0200000082"))
    self.assertEqual(packets[1][:7], bytes.fromhex("000c0300000001"))
    self.assertEqual(packets[2][:7], bytes.fromhex("000c0300000002"))
    self.assertEqual(b"".join(p[7:] for p in packets), payload)
    self.assertEqual(usb_total_length(packets[0]), 130)

  def test_empty_payload_rejected(self):
    with self.assertRaises(ValueError):
      usb_wrap_payload(b"")

  def test_packet_type_requires_header(self):
    self.assertEqual(usb_packet_type(bytes.fromhex("000c010000000180")), 0x01)
    with self.assertRaises(FrameError):
      usb_packet_type(STATUS_QUERY_FRAME)


if __name__ == "__main__":
  unittest.main()
