import enum
from dataclasses import dataclass


@dataclass
class Step:
  """Represents a single step in a thermocycler profile."""

  temperature: float
  hold_seconds: float


class LidStatus(enum.Enum):
  """Temperature status of the thermocycler lid."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


class BlockStatus(enum.Enum):
  """Temperature status of the thermocycler block."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"
