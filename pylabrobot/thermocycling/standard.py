import enum
from dataclasses import dataclass
from typing import List, Optional, Union


@dataclass
class Step:
  """Represents a single step in a thermocycler profile."""

  temperature: List[float]
  hold_seconds: float
  rate: Optional[float] = None  # degrees Celsius per second

  def serialize(self) -> dict:
    return {
      "temperature": self.temperature,
      "hold_seconds": self.hold_seconds,
      "rate": self.rate,
    }


@dataclass
class Stage:
  """Represents a single stage in a thermocycler protocol."""

  steps: List[Step]
  repeats: int

  def serialize(self) -> dict:
    return {
      "steps": [step.serialize() for step in self.steps],
      "repeats": self.repeats,
    }


@dataclass
class Protocol:
  """Represents a thermocycler protocol ("cycle") with multiple stages."""

  stages: List[Union[Stage, Step]]

  def serialize(self) -> dict:
    return {
      "stages": [stage.serialize() for stage in self.stages],
    }

  @classmethod
  def deserialize(cls, data: dict) -> 'Protocol':
    stages = []
    for stage_data in data.get("stages", []):
      if "steps" in stage_data:
        steps = [Step(**step) for step in stage_data["steps"]]
        stages.append(Stage(steps=steps, repeats=stage_data.get("repeats", 1)))
      else:
        stages.append(Step(**stage_data))
    return cls(stages=stages)


class LidStatus(enum.Enum):
  """Temperature status of the thermocycler lid."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


class BlockStatus(enum.Enum):
  """Temperature status of the thermocycler block."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"
