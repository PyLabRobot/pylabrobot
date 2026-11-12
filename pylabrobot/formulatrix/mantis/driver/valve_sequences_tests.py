"""Valve sequence tests: a valve state line as the chip valve bank's state."""

import os
import tempfile
import unittest
from typing import List, Optional, Tuple

from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError
from pylabrobot.formulatrix.mantis.driver.valve_sequences import (
  compute_valve_bank_state,
  parse_valve_sequence,
)

CLOSED_OUTPUTS, OPEN_OUTPUTS = "0" * 96, "1" * 96
CLOSED_INPUTS = "0" * 16


def get_valve_state_line(
  *,
  duration: int,
  open_valves: Tuple[int, ...] = (),
  outputs_open: bool = False,
  length: int = 18,
  inputs: str = CLOSED_INPUTS,
) -> str:
  """A synthetic valve state line, with the head's valves given open."""
  valves = "".join("1" if place in open_valves else "0" for place in range(length))
  outputs = OPEN_OUTPUTS if outputs_open else CLOSED_OUTPUTS
  return f"ValveStateSequenceItem %00 {duration:04d} {valves} {outputs} {inputs}\r\n"


def parse_text(*, text: str) -> Optional[List[Tuple[int, int]]]:
  with tempfile.TemporaryDirectory() as directory:
    path = os.path.join(directory, "sequence.seq.txt")
    with open(path, "w", encoding="utf-8", newline="") as f:
      f.write(text)
    return parse_valve_sequence(path=path)


class ValveBankStateTests(unittest.TestCase):
  def test_each_valve_clears_its_bit_when_open(self):
    cases = [
      ((), False, 31),
      ((0,), False, 23),  # small
      ((1,), False, 27),  # large
      ((2,), False, 30),  # fill
      ((), True, 29),  # any output
      ((0, 1, 2), True, 16),
    ]
    for open_valves, outputs_open, state in cases:
      with self.subTest(open_valves=open_valves, outputs_open=outputs_open):
        valves = "".join("1" if place in open_valves else "0" for place in range(18))
        outputs = OPEN_OUTPUTS if outputs_open else CLOSED_OUTPUTS
        self.assertEqual(compute_valve_bank_state(valves=valves, outputs=outputs), state)

  def test_one_output_open_is_outputs_open(self):
    self.assertEqual(compute_valve_bank_state(valves="0" * 17, outputs="0" * 95 + "1"), 29)


class ParseValveSequenceTests(unittest.TestCase):
  def test_states_and_durations_in_order(self):
    # Every valve the bank does not encode is ignored; the head's string may have 17 places.
    text = (
      "﻿"
      + get_valve_state_line(duration=1, open_valves=(2, 6))
      + get_valve_state_line(duration=38, open_valves=(0, 2, 6))
      + "\r\n"
      + get_valve_state_line(duration=15, open_valves=(0, 6), outputs_open=True)
      + get_valve_state_line(duration=100, open_valves=(16,), length=17)
      + "RemarkSequenceItem Safe%32State \r\n"
      + get_valve_state_line(duration=700, open_valves=(0, 1, 17))
    )
    self.assertEqual(parse_text(text=text), [(30, 1), (22, 38), (21, 15), (31, 100), (19, 700)])

  def test_an_empty_file_is_an_empty_sequence(self):
    self.assertEqual(parse_text(text="﻿\r\n"), [])

  def test_a_file_with_another_line_is_no_valve_sequence(self):
    for line in (
      "MoveSequenceItem %00 True X 15 Y 30 Z 0\r\n",
      "TimedValveStateSequenceItem CustomDelay True 0018 000 000 000\r\n",
    ):
      with self.subTest(line=line):
        self.assertIsNone(parse_text(text=get_valve_state_line(duration=10) + line))

  def test_a_line_it_cannot_set_refuses(self):
    cases = [
      (get_valve_state_line(duration=0), r"duration 0 is not in \(1, 32767\)"),
      (get_valve_state_line(duration=10, open_valves=(4,)), r"head valves \[4\] have no known"),
      (
        get_valve_state_line(duration=10, inputs="1" + "0" * 15),
        "an input valve has no known state",
      ),
      ("ValveStateSequenceItem %00 0010 000\r\n", "a message, duration and 3 strings"),
      (get_valve_state_line(duration=10).replace(" 0010 ", " 00x0 "), "is not a number"),
      (get_valve_state_line(duration=10, length=2), "string is too short"),
      (get_valve_state_line(duration=10).replace(CLOSED_INPUTS, "0" * 15 + "2"), "0s and 1s"),
    ]
    for text, message in cases:
      with self.subTest(message=message):
        with self.assertRaisesRegex(MantisConfigurationError, message):
          parse_text(text=text)


if __name__ == "__main__":
  unittest.main()
