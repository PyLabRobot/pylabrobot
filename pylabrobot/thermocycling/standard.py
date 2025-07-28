import enum
from dataclasses import dataclass
from typing import List, Optional, Union


@dataclass
class Step:
  """Represents a single step in a thermocycler profile."""

  temperature: float
  hold_seconds: float
  rate: Optional[float] = None  # degrees Celsius per second


@dataclass
class Stage:
  """Represents a single stage in a thermocycler protocol."""

  steps: List[Step]
  repeats: int


@dataclass
class Protocol:
  """Represents a thermocycler protocol ("cycle") with multiple stages."""

  stages: List[Union[Stage, Step]]


class LidStatus(enum.Enum):
  """Temperature status of the thermocycler lid."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


class BlockStatus(enum.Enum):
  """Temperature status of the thermocycler block."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"
