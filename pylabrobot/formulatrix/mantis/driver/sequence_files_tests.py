"""Sequence file tests: the move lines of a sequence, in its files' layout."""

import os
import tempfile
import unittest

from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError
from pylabrobot.formulatrix.mantis.driver.sequence_files import parse_move_sequence

# A route in the layout of a v4 install's station sequences, with synthetic positions: a remark, a
# line naming no axis, X and Y, Z alone, all three, and a named target position.
SYNTHETIC_ROUTE = (
  "RemarkSequenceItem Move%32To%32Somewhere \r\n"
  "MoveSequenceItem %00 True  0\r\n"
  "MoveSequenceItem %00 True X 15.000 Y 30.000  0\r\n"
  "MoveSequenceItem %00 True Z -1.570  0\r\n"
  "MoveSequenceItem %00 True X -50.000 Y 20.000 Z -1.570  0\r\n"
  "MoveSequenceItem %00 True X -60.000 Y 10.000 Z 30.000  0 TargetPosition SomeLocation\r\n"
)


class ParseMoveSequenceTests(unittest.TestCase):
  def parse(self, text: str):
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "route.seq.txt")
      with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
      return parse_move_sequence(path=path)

  def test_each_move_line_gives_the_axes_it_names(self):
    self.assertEqual(
      self.parse(SYNTHETIC_ROUTE),
      [
        (None, None, None),
        (15.0, 30.0, None),
        (None, None, -1.57),
        (-50.0, 20.0, -1.57),
        (-60.0, 10.0, 30.0),
      ],
    )

  def test_a_line_without_a_delay_and_a_byte_order_mark_are_read(self):
    text = "﻿MoveSequenceItem %00 True Z -1.570 0\r\nMoveSequenceItem %00 True X 15 Y 30 Z 1"
    self.assertEqual(self.parse(text), [(None, None, -1.57), (15.0, 30.0, 1.0)])

  def test_lines_that_are_not_supported_refuse(self):
    cases = {
      "MoveSequenceItem %00 True X 15.000  0": "one of X and Y alone",
      "MoveSequenceItem %00 True dX 1.000 dY 1.000  0": "relative move",
      "MoveSequenceItem %00 False X 15.000 Y 30.000  0": "does not wait",
      "MoveSequenceItem %00 True X 15.000 Y 30.000  500": "delay other than 0",
      "MoveSequenceItem %00 True X fifteen Y 30.000  0": "is no number",
      "ValveStateSequenceItem %00 10 00000000000000000": "does not belong",
    }
    for line, message in cases.items():
      with self.subTest(line=line):
        with self.assertRaisesRegex(MantisConfigurationError, message):
          self.parse(line + "\r\n")


if __name__ == "__main__":
  unittest.main()
