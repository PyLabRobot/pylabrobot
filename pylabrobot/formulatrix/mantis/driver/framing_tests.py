"""Framing tests, against frames recorded on the link of a Mantis v4."""

import unittest

from pylabrobot.formulatrix.mantis.driver.errors import ChecksumError, FrameError
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame, split_frames

# Host frames: (frame, packet number, board, command, payload).
RECORDED_HOST_FRAMES = {
  "version read, board 0": ("0c00000000000000000001000d00", 0, 0, 0x0001, ""),
  "limits read, motor 2": ("0e0000000e00000000000a0002000800", 14, 0, 0x000A, "0200"),
  "two-word payload": ("100000000c00000000003800230001000600", 12, 0, 0x0038, "23000100"),
  "board 12": ("0e0000000f0000000c00100000001d00", 15, 12, 0x0010, "0000"),
  "high packet number": (
    "12000000816d000000002900780001000000c36d",
    0x6D81,
    0,
    0x0029,
    "780001000000",
  ),
}
# Device frames: (frame, packet number, board, code, payload).
RECORDED_REPLY = ("0c000000816d0000000000008d6d", 0x6D81, 0, 0x0000, "")
RECORDED_UNANSWERED_FRAME = ("0c000000000000000000ff7ff37f", 0, 0, 0x7FFF, "")


class BuildFrameTests(unittest.TestCase):
  def test_reproduces_every_recorded_host_frame(self):
    for key, (frame, packet_number, board, command, payload) in RECORDED_HOST_FRAMES.items():
      with self.subTest(key=key):
        built = build_frame(
          packet_number=packet_number,
          board=board,
          command=command,
          payload=bytes.fromhex(payload),
        )
        self.assertEqual(built.hex(), frame)


class ParseFrameTests(unittest.TestCase):
  def test_every_recorded_frame(self):
    for frame, packet_number, board, code, payload in [
      *RECORDED_HOST_FRAMES.values(),
      RECORDED_REPLY,
      RECORDED_UNANSWERED_FRAME,
    ]:
      with self.subTest(frame=frame):
        self.assertEqual(
          parse_frame(frame=bytes.fromhex(frame)),
          (packet_number, board, code, bytes.fromhex(payload)),
        )

  def test_wrong_check_word(self):
    frame = bytearray.fromhex(RECORDED_REPLY[0])
    frame[-1] ^= 0x01
    with self.assertRaises(ChecksumError):
      parse_frame(frame=bytes(frame))

  def test_malformed_frames(self):
    reply = bytes.fromhex(RECORDED_REPLY[0])
    with self.subTest(case="too short"), self.assertRaises(FrameError):
      parse_frame(frame=bytes(12))
    with self.subTest(case="size field disagrees"), self.assertRaises(FrameError):
      parse_frame(frame=reply + bytes(2))


class SplitFramesTests(unittest.TestCase):
  def test_frames_back_to_back_and_a_partial_one(self):
    reply = bytes.fromhex(RECORDED_REPLY[0])
    other = bytes.fromhex(RECORDED_UNANSWERED_FRAME[0])
    buffer = bytearray(reply + other + reply[:5])
    self.assertEqual(split_frames(buffer=buffer), [reply, other])
    self.assertEqual(bytes(buffer), reply[:5])
    buffer.extend(reply[5:])
    self.assertEqual(split_frames(buffer=buffer), [reply])
    self.assertEqual(buffer, bytearray())

  def test_a_size_no_frame_can_have_drops_one_byte_at_a_time(self):
    reply = bytes.fromhex(RECORDED_REPLY[0])
    for case, garbage in (
      ("below the header", bytes.fromhex("0a000000")),
      ("odd", bytes.fromhex("0d000000")),
      ("over the maximum", bytes.fromhex("0e")),
    ):
      with self.subTest(case=case):
        buffer = bytearray(garbage + reply)
        with self.assertLogs("pylabrobot.formulatrix.mantis.driver.framing", level="WARNING"):
          self.assertEqual(split_frames(buffer=buffer), [reply])
        self.assertEqual(buffer, bytearray())


if __name__ == "__main__":
  unittest.main()
