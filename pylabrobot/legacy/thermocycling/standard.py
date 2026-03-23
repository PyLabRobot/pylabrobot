import enum
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.serializer import SerializableMixin


@dataclass
class Step(SerializableMixin):
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
class Stage(SerializableMixin):
  """Represents a single stage in a thermocycler protocol."""

  steps: List[Step]
  repeats: int

  def serialize(self) -> dict:
    return {
      "steps": [step.serialize() for step in self.steps],
      "repeats": self.repeats,
    }


@dataclass
class Protocol(SerializableMixin):
  """Represents a thermocycler protocol ("cycle") with multiple stages."""

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
  """Temperature status of the thermocycler lid."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


class BlockStatus(enum.Enum):
  """Temperature status of the thermocycler block."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


# ---------------------------------------------------------------------------
# Translation between legacy and new types
# ---------------------------------------------------------------------------


def protocol_to_new(protocol: Protocol):
  """Convert a legacy Protocol to a new-architecture Protocol."""
  from pylabrobot.capabilities.thermocycling import standard as new

  return new.Protocol(
    stages=[
      new.Stage(
        steps=[
          new.Step(
            temperature=list(step.temperature),
            hold_seconds=step.hold_seconds,
            rate=step.rate,
          )
          for step in stage.steps
        ],
        repeats=stage.repeats,
      )
      for stage in protocol.stages
    ]
  )


def protocol_from_new(new_protocol) -> Protocol:
  """Convert a new-architecture Protocol to a legacy Protocol."""
  return Protocol(
    stages=[
      Stage(
        steps=[
          Step(
            temperature=list(step.temperature),
            hold_seconds=step.hold_seconds,
            rate=step.rate,
          )
          for step in stage.steps
        ],
        repeats=stage.repeats,
      )
      for stage in new_protocol.stages
    ]
  )
