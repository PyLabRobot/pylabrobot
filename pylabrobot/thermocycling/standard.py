import enum
from dataclasses import dataclass
from typing import List


@dataclass
class Step:
  """Represents a single step in a thermocycler profile."""

  temperature: List[float]
  hold_seconds: float


class LidStatus(enum.Enum):
  """Temperature status of the thermocycler lid."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


class BlockStatus(enum.Enum):
  """Temperature status of the thermocycler block."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"
