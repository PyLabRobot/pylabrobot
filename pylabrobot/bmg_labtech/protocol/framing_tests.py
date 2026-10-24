"""FTDI framing tests, against frames recorded from a CLARIOstar."""

import unittest

from pylabrobot.bmg_labtech.protocol.errors import ChecksumError, FrameError
from pylabrobot.bmg_labtech.protocol.framing import (
  ftdi_extract_payload,
  ftdi_validate_frame,
  ftdi_wrap_payload,
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


if __name__ == "__main__":
  unittest.main()
