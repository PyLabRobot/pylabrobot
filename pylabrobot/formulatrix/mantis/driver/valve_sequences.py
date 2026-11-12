"""A chip's valve sequences: a ``.seq.txt`` file's valve states as the chip valve bank's states.

A valve state line holds its message, its duration in the sequencer's time steps, then three
strings of 0 and 1: the head's valves, the 96 output valves and the 16 input valves. The bank
(PPI address 40) takes one state per line; it is not a copy of the strings. Of the head's valves,
the small, large and fill valves each clear a bit when open, any output valve open clears another,
and bit 4 is always set: 31 closes everything.
"""

import re
from typing import List, Optional, Tuple

from .errors import MantisConfigurationError
from .sequence_files import REMARK_ITEM

VALVE_STATE_ITEM = "ValveStateSequenceItem"
# The head's valves the bank's state encodes, by their place in the line's first string.
SMALL_VALVE, LARGE_VALVE, FILL_VALVE = 0, 1, 2
# Other places a chip's files set; the recorded v4 states are the same without them.
UNENCODED_VALVES = (3, 6, 15, 16, 17)
# The bank's bits: each set while its valves are closed; ``ALWAYS_SET`` is set in every state.
FILL_CLOSED, OUTPUTS_CLOSED, LARGE_CLOSED, SMALL_CLOSED, ALWAYS_SET = 1, 2, 4, 8, 16
# The sequences a chip's folder holds that only set valves, by name: its file stem.
VALVE_SEQUENCE_FILES = {
  "dispense_100nl": "dispense_100nl",
  "dispense_500nl": "dispense_500nl",
  "dispense_1ul": "dispense_1ul",
  "dispense_5ul": "dispense_5ul",
  "prime_pump": "PrimePump",
  "post_prime": "PostPrime",
  "pre_attach": "PreAttach",
  "pre_detach": "PreDetach",
  "reverse_pump": "ReversePump",
  "safe_state": "SafeState",
}
# A state's duration is an int16 on the wire, and 0 holds nothing.
DURATION_RANGE = (1, 0x7FFF)


def compute_valve_bank_state(*, valves: str, outputs: str) -> int:
  """The valve bank's state for a line's head valves and output valves.

  Args:
    valves: the line's first string: "1" where a head valve is open.
    outputs: the line's second string: "1" where an output valve is open.
  """
  state = ALWAYS_SET
  for place, closed in (
    (SMALL_VALVE, SMALL_CLOSED),
    (LARGE_VALVE, LARGE_CLOSED),
    (FILL_VALVE, FILL_CLOSED),
  ):
    if valves[place] != "1":
      state |= closed
  if "1" not in outputs:
    state |= OUTPUTS_CLOSED
  return state


def _parse_valve_state_line(*, path: str, number: int, tokens: List[str]) -> Tuple[int, int]:
  """One valve state line's (state, duration), from the tokens after its class name.

  Raises:
    MantisConfigurationError: if the line is malformed, its duration out of ``DURATION_RANGE``,
      it opens a head valve the state does not encode or an input valve.
  """
  where = f"{path}, line {number}"
  if len(tokens) != 5:
    raise MantisConfigurationError(f"{where}: a valve state has a message, duration and 3 strings")
  _, duration_text, valves, outputs, inputs = tokens
  if not duration_text.isdigit():
    raise MantisConfigurationError(f"{where}: duration {duration_text!r} is not a number")
  duration = int(duration_text)
  if not DURATION_RANGE[0] <= duration <= DURATION_RANGE[1]:
    raise MantisConfigurationError(f"{where}: duration {duration} is not in {DURATION_RANGE}")
  if any(re.fullmatch(r"[01]+", text) is None for text in (valves, outputs, inputs)):
    raise MantisConfigurationError(f"{where}: a valve string is not 0s and 1s")
  if len(valves) <= FILL_VALVE:
    raise MantisConfigurationError(f"{where}: the head's valve string is too short")
  encoded = (SMALL_VALVE, LARGE_VALVE, FILL_VALVE, *UNENCODED_VALVES)
  opened = [place for place, valve in enumerate(valves) if valve == "1" and place not in encoded]
  if opened:
    raise MantisConfigurationError(f"{where}: head valves {opened} have no known state")
  if "1" in inputs:
    raise MantisConfigurationError(f"{where}: an input valve has no known state")
  return compute_valve_bank_state(valves=valves, outputs=outputs), duration


def parse_valve_sequence(*, path: str) -> Optional[List[Tuple[int, int]]]:
  """A valve sequence file's (state, duration) per valve state line, in order.

  Remark lines and blank lines are skipped; an empty file is an empty sequence. A file holding any
  other line, such as a move, is not a valve sequence: None.

  Args:
    path: the ``.seq.txt`` file.

  Raises:
    MantisConfigurationError: if a valve state line is not supported (``_parse_valve_state_line``).
  """
  states: List[Tuple[int, int]] = []
  # utf-8-sig drops a byte-order mark at the start; one within the file is dropped per line.
  with open(path, encoding="utf-8-sig") as f:
    for number, line in enumerate(f, start=1):
      tokens = line.replace("\ufeff", "").split()
      if not tokens or tokens[0] == REMARK_ITEM:
        continue
      if tokens[0] != VALVE_STATE_ITEM:
        return None
      states.append(_parse_valve_state_line(path=path, number=number, tokens=tokens[1:]))
  return states
