import enum
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.serializer import SerializableMixin


@dataclass
class Step(SerializableMixin):
  """A single step in a thermocycler profile."""

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
class Stage(SerializableMixin):
  """A stage in a thermocycler protocol: a list of steps repeated N times."""

  steps: List[Step]
  repeats: int

  def serialize(self) -> dict:
    return {
      "steps": [step.serialize() for step in self.steps],
      "repeats": self.repeats,
    }


@dataclass
class Protocol(SerializableMixin):
  """A thermocycler protocol: a list of stages."""

  stages: List[Stage]

  def serialize(self) -> dict:
    return {
      "stages": [stage.serialize() for stage in self.stages],
    }

  @classmethod
  def deserialize(cls, data: dict) -> "Protocol":
    stages = []
    for stage_data in data.get("stages", []):
      steps = [Step(**step) for step in stage_data["steps"]]
      stages.append(Stage(steps=steps, repeats=stage_data.get("repeats", 1)))
    return cls(stages=stages)


class LidStatus(enum.Enum):
  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


class BlockStatus(enum.Enum):
  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"
