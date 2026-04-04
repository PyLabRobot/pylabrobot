"""Parser for Formulatrix Mantis sequence files.

Sequence files are line-based text files where each line starts with a class name
followed by whitespace-separated tokens.  This module tokenizes and parses those
lines into typed sequence-item objects.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Dict, List, Optional, Type


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ValveState(Enum):
  """Open/closed state of a valve."""

  CLOSED = 0
  OPEN = 1

  def __str__(self) -> str:
    return "Open" if self == ValveState.OPEN else "Closed"


class ValveName(Enum):
  """Named valves on the Mantis chip carrier."""

  SMALL = 0
  LARGE = 1
  FILL = 2
  PURGE = 3
  WASTE = 4
  WASH_PUMP = 5
  OVERFLOW = 6
  AIR_PURGE = 7
  INPUT_AIR_VENT = 8
  WASTE_CLEAR = 9
  INPUT_PRESSURE_SELECT = 10
  WASH_STATION_PUMP = 11
  WASH_INPUT_SELECT = 14
  PRESSURE_VACUUM_SWITCH = 17
  PLATE_STACKER = 18
  PLATE_STACKER_LATCH_1 = 19
  PLATE_STACKER_LATCH_2 = 20
  OUTPUT = 21
  INPUT = 22


class MoveType(Enum):
  """Type of move command."""

  ABSOLUTE = 0
  RELATIVE = 1


class PressureType(Enum):
  """Pressure controller identifiers."""

  CHIP = 0
  BOTTLE = 1
  VACUUM = 2
  WASH = 3
  WASH_VACUUM = 4
  PRIME_PRESSURE = 5
  PRIME_VACUUM = 6
  RECOVERY_PRESSURE = 7
  RECOVERY_VACUUM = 8

  @staticmethod
  def from_string(s: str) -> "PressureType":
    for member in PressureType:
      if member.name.lower() == s.lower():
        return member
    raise ValueError(f"Unknown PressureType: {s}")


class SequenceItemQueueingType(Enum):
  """How the sequencer handles this item."""

  NON_QUEUED_NON_BLOCKING = 0
  NON_QUEUED_BLOCKING = 1
  QUEUED = 2


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class Token:
  """A single whitespace-delimited token from a sequence line."""

  def __init__(self, text: str) -> None:
    self.text = text

  def decode_string(self) -> str:
    if self.text == "%00":
      return ""

    def _replace_match(match: re.Match) -> str:
      return chr(int(match.group(1)))

    return re.sub(r"%(\\d{2})", _replace_match, self.text)

  def to_integer(self) -> int:
    return int(self.text)

  def to_double(self) -> float:
    return float(self.text)

  def to_bool(self) -> bool:
    return self.text.lower() == "true"

  def to_string(self) -> str:
    return self.text


class StringTokenizer:
  """Splits a line into :class:`Token` objects and iterates over them."""

  def __init__(self, data: str) -> None:
    self.tokens = [Token(t) for t in data.split()]
    self.current_index = 0

  def has_more_tokens(self) -> bool:
    return self.current_index < len(self.tokens)

  def next(self) -> Token:
    if not self.has_more_tokens():
      raise RuntimeError("Expected a token when none was found.")
    token = self.tokens[self.current_index]
    self.current_index += 1
    return token


# ---------------------------------------------------------------------------
# Valve device state
# ---------------------------------------------------------------------------


class ValveDeviceState:
  """Decoded valve state from a sequence line's bitstring tokens."""

  OUTPUT_SIZE = 96
  INPUT_SIZE = 16
  FILL_VALVE_CONTROL_COUNT = 3

  def __init__(self, tokenizer: Optional[StringTokenizer] = None) -> None:
    self.main_valves: Dict[ValveName, ValveState] = {}
    self.fill: List[ValveState] = [ValveState.CLOSED] * self.FILL_VALVE_CONTROL_COUNT
    self.output: List[ValveState] = [ValveState.CLOSED] * self.OUTPUT_SIZE
    self.input: List[ValveState] = [ValveState.CLOSED] * self.INPUT_SIZE

    if tokenizer:
      self._parse(tokenizer)

  def _parse(self, tokenizer: StringTokenizer) -> None:
    # 1st token: main valves + fill
    str1 = tokenizer.next().to_string()
    for i, char in enumerate(str1):
      state = ValveState.OPEN if char == "1" else ValveState.CLOSED
      if i in (12, 13):
        if len(self.fill) > (i - 11):
          self.fill[i - 11] = state
      elif i in (15, 16):
        pass
      else:
        try:
          vn = ValveName(i)
          if vn == ValveName.FILL:
            self.fill[0] = state
          else:
            self.main_valves[vn] = state
        except ValueError:
          pass

    # 2nd token: output valves
    if tokenizer.has_more_tokens():
      str2 = tokenizer.next().to_string()
      for i, char in enumerate(str2):
        if i < len(self.output):
          self.output[i] = ValveState.OPEN if char == "1" else ValveState.CLOSED

    # 3rd token: input valves
    if tokenizer.has_more_tokens():
      str3 = tokenizer.next().to_string()
      for i, char in enumerate(str3):
        if i < len(self.input):
          self.input[i] = ValveState.OPEN if char == "1" else ValveState.CLOSED


# ---------------------------------------------------------------------------
# Sequence items
# ---------------------------------------------------------------------------


class SequenceItem:
  """Base class for a single parsed sequence line."""

  def __init__(self, tokenizer: StringTokenizer) -> None:
    self.status_message = tokenizer.next().decode_string()
    self.queueing_type = SequenceItemQueueingType.QUEUED
    self.delay = 0


class ValveStateSequenceItem(SequenceItem):
  def __init__(self, tokenizer: StringTokenizer) -> None:
    super().__init__(tokenizer)
    self.delay = tokenizer.next().to_integer()
    self.device_state = ValveDeviceState(tokenizer)


class MoveSequenceItem(SequenceItem):
  def __init__(self, tokenizer: StringTokenizer) -> None:
    super().__init__(tokenizer)
    self.wait = tokenizer.next().to_bool()
    self.x: Optional[float] = None
    self.y: Optional[float] = None
    self.z: Optional[float] = None
    self.move_type = MoveType.ABSOLUTE

    while tokenizer.has_more_tokens():
      token = tokenizer.next()
      s = token.decode_string().lower()
      if s == "x":
        self.x = tokenizer.next().to_double()
        self.move_type = MoveType.ABSOLUTE
      elif s == "y":
        self.y = tokenizer.next().to_double()
        self.move_type = MoveType.ABSOLUTE
      elif s == "z":
        self.z = tokenizer.next().to_double()
        self.move_type = MoveType.ABSOLUTE
      elif s == "dx":
        self.x = tokenizer.next().to_double()
        self.move_type = MoveType.RELATIVE
      elif s == "dy":
        self.y = tokenizer.next().to_double()
        self.move_type = MoveType.RELATIVE
      elif s == "dz":
        self.z = tokenizer.next().to_double()
        self.move_type = MoveType.RELATIVE
      else:
        try:
          self.delay = int(s)
        except ValueError:
          pass


class AirPumpSequenceItem(SequenceItem):
  def __init__(self, tokenizer: StringTokenizer) -> None:
    super().__init__(tokenizer)
    self.queueing_type = SequenceItemQueueingType.NON_QUEUED_BLOCKING
    val = tokenizer.next().decode_string().lower()
    self.state = ValveState.OPEN if val == "on" else ValveState.CLOSED
    self.timer = tokenizer.next().to_integer() if tokenizer.has_more_tokens() else 0


class RemarkSequenceItem(SequenceItem):
  def __init__(self, tokenizer: StringTokenizer) -> None:
    super().__init__(tokenizer)
    if tokenizer.has_more_tokens() and tokenizer.next().to_string().lower() == "blocking":
      self.queueing_type = SequenceItemQueueingType.NON_QUEUED_BLOCKING
    else:
      self.queueing_type = SequenceItemQueueingType.NON_QUEUED_NON_BLOCKING


class PressureRegulatorSequenceItem(SequenceItem):
  def __init__(self, tokenizer: StringTokenizer) -> None:
    super().__init__(tokenizer)
    self.pressure_type = PressureType.from_string(tokenizer.next().to_string())
    self.delay = tokenizer.next().to_integer()
    self.pressure = tokenizer.next().to_string()
    self.ignore_warning = tokenizer.next().to_bool() if tokenizer.has_more_tokens() else False
    self.queueing_type = SequenceItemQueueingType.NON_QUEUED_BLOCKING


class MantisActiveAccPortIndexSequenceItem(SequenceItem):
  def __init__(self, tokenizer: StringTokenizer) -> None:
    super().__init__(tokenizer)
    self.queueing_type = SequenceItemQueueingType.NON_QUEUED_NON_BLOCKING
    self.acc_port_index = tokenizer.next().to_integer()
    self.input_id = tokenizer.next().to_string() if tokenizer.has_more_tokens() else ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

CLASS_MAP: Dict[str, Type[SequenceItem]] = {
  "ValveStateSequenceItem": ValveStateSequenceItem,
  "MoveSequenceItem": MoveSequenceItem,
  "AirPumpSequenceItem": AirPumpSequenceItem,
  "RemarkSequenceItem": RemarkSequenceItem,
  "PressureRegulatorSequenceItem": PressureRegulatorSequenceItem,
  "MantisActiveAccPortIndexSequenceItem": MantisActiveAccPortIndexSequenceItem,
}


def parse_sequence_file(file_path: str) -> List[SequenceItem]:
  """Parse a Mantis sequence file into a list of :class:`SequenceItem` objects."""
  items: List[SequenceItem] = []
  with open(file_path, "r", encoding="utf-8-sig") as f:
    for line in f:
      line = line.strip()
      if not line or line.startswith("//"):
        continue
      tokenizer = StringTokenizer(line)
      if not tokenizer.has_more_tokens():
        continue
      class_name = tokenizer.next().to_string()
      if class_name in CLASS_MAP:
        items.append(CLASS_MAP[class_name](tokenizer))
  return items
