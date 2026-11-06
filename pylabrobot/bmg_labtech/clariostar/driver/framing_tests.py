"""Framing tests, against the frames recorded in the driver and drawer tests."""

import unittest

from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  VERIFICATION_DATA_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.drawer_tests import (
  CLOSE_DRAWER_FRAME,
  CLOSE_DRAWER_REPLY,
  OPEN_DRAWER_FRAME,
  OPEN_DRAWER_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.framing import FTDI_HEADER
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
from pylabrobot.bmg_labtech.protocol.errors import ChecksumError, FrameError
from pylabrobot.bmg_labtech.protocol.framing import (
  ftdi_extract_payload,
  ftdi_validate_frame,
  ftdi_wrap_payload,
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
      self.assertEqual(ftdi_wrap_payload(ftdi_extract_payload(sent), header=FTDI_HEADER), sent)
    self.assertEqual(ftdi_wrap_payload(b"\x80", header=FTDI_HEADER), STATUS_QUERY_FRAME)

  def test_every_real_frame_validates(self):
    for sent, received in REAL_EXCHANGES:
      ftdi_validate_frame(sent, header=FTDI_HEADER)
      ftdi_validate_frame(received, header=FTDI_HEADER)
      self.assertEqual(len(ftdi_extract_payload(received)), len(received) - 8)

  def test_real_truncated_reply_fails(self):
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_TRUNCATED, header=FTDI_HEADER)

  def test_a_checksum_past_16_bits_validates(self):
    # The 96-well verification read's data reply: its checksum is 0x01c462.
    self.assertGreater(int.from_bytes(VERIFICATION_DATA_REPLY[-4:-1], "big"), 0xFFFF)
    ftdi_validate_frame(VERIFICATION_DATA_REPLY, header=FTDI_HEADER)

  def test_corrupted_byte_fails_checksum(self):
    # Not a reader's reply: the recorded frame with one bit flipped.
    frame = bytearray(STATUS_REPLY_FRAME)
    frame[6] ^= 0x01
    with self.assertRaises(ChecksumError):
      ftdi_validate_frame(bytes(frame), header=FTDI_HEADER)

  def test_truncated_frame_fails(self):
    # Not a reader's reply: the recorded frame cut short.
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_REPLY_FRAME[:-1], header=FTDI_HEADER)
    with self.assertRaises(FrameError):
      ftdi_validate_frame(STATUS_REPLY_FRAME[:5], header=FTDI_HEADER)


if __name__ == "__main__":
  unittest.main()
