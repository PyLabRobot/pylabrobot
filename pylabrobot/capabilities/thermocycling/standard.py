"""Standard types for thermocycler protocols.

Defines the abstract protocol model:
- Overshoot / Ramp: step transition profile
- Step / Stage / Protocol: hierarchical cycle description
- LidStatus / BlockStatus: runtime state enums

These types are backend-agnostic. Backend-specific parameters
attach to Step.backend_params (BackendParams subclass).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from pylabrobot.serializer import SerializableMixin

if TYPE_CHECKING:
  from pylabrobot.capabilities.capability import BackendParams


@dataclass(frozen=True)
class Overshoot:
  """Transient temperature excursion during a step transition.

  The backend decides how to honor this: use a native device overshoot
  construct (ODTC), insert an explicit intermediate step, or ignore it.
  When not specified, the backend computes overshoot from hardware physics
  and the requested ramp rate.

  Args:
    target_temp: Peak temperature to briefly reach (°C).
    hold_seconds: Time to spend at the peak (seconds).
    return_rate: Ramp rate falling back to the step target (°C/s).
  """

  target_temp: float
  hold_seconds: float
  return_rate: float


@dataclass(frozen=True)
class Ramp:
  """Transition profile into a step's target temperature.

  Usage:
    Ramp()                           # full device speed, no overshoot
    Ramp(rate=5.0)                   # linear 5 °C/s
    Ramp(rate=5.0, overshoot=...)    # fast ramp with managed overshoot

  Args:
    rate: Ramp rate in °C/s. ``float('inf')`` means as fast as the
      device allows (the default).
    overshoot: Optional overshoot hint. If None, the backend decides
      whether and how to overshoot based on hardware physics.
  """

  rate: float = float("inf")
  overshoot: Optional[Overshoot] = None


FULL_SPEED = Ramp()
"""Canonical zero-boilerplate Ramp: full device speed, no overshoot."""


@dataclass
class Step(SerializableMixin):
  """A single temperature hold in a thermocycler profile.

  Args:
    temperature: Target block temperature in °C.
    hold_seconds: Finite positive number of seconds to hold at the target
      temperature. Must be a real number (e.g. 30, 300). For an indefinite
      hold after a protocol completes, use the device's post-heating
      mechanism instead (e.g. ``post_heating=True`` on ``ODTCBackendParams``
      or ``ODTCProtocol`` for the ODTC).
    ramp: Transition profile into this step's temperature.
      Defaults to FULL_SPEED (full device speed, no overshoot).
    lid_temperature: Optional lid/cover target temperature in °C.
      None means use the Stage or Protocol default.
    backend_params: Optional backend-specific per-step parameters
      (e.g. ``ODTCThermocyclerBackend.StepParams``). Opaque to PLR core.
  """

  temperature: float
  hold_seconds: float
  ramp: Ramp = field(default_factory=Ramp)
  lid_temperature: Optional[float] = None
  backend_params: Optional["BackendParams"] = None

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "temperature": self.temperature,
      "hold_seconds": self.hold_seconds,
      "ramp": {
        "rate": self.ramp.rate,
        "overshoot": {
          "target_temp": self.ramp.overshoot.target_temp,
          "hold_seconds": self.ramp.overshoot.hold_seconds,
          "return_rate": self.ramp.overshoot.return_rate,
        }
        if self.ramp.overshoot is not None
        else None,
      },
      "lid_temperature": self.lid_temperature,
    }

  @classmethod
  def deserialize(cls, data: dict) -> "Step":
    ramp_data = data.get("ramp", {})
    overshoot_data = ramp_data.get("overshoot")
    overshoot = (
      Overshoot(
        target_temp=overshoot_data["target_temp"],
        hold_seconds=overshoot_data["hold_seconds"],
        return_rate=overshoot_data["return_rate"],
      )
      if overshoot_data is not None
      else None
    )
    ramp = Ramp(rate=ramp_data.get("rate", float("inf")), overshoot=overshoot)
    return cls(
      temperature=data["temperature"],
      hold_seconds=data["hold_seconds"],
      ramp=ramp,
      lid_temperature=data.get("lid_temperature"),
    )


@dataclass
class Stage(SerializableMixin):
  """A set of steps that repeats a fixed number of times.

  Args:
    steps: The ordered steps in this stage.
    repeats: Number of times the stage repeats (default 1).
    inner_stages: Nested child stages for complex cycling patterns
      (e.g. inner PCR loop inside an outer denaturation loop).
      Empty list means no nesting.
  """

  steps: List[Step]
  repeats: int = 1
  inner_stages: List["Stage"] = field(default_factory=list)

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "steps": [s.serialize() for s in self.steps],
      "repeats": self.repeats,
      "inner_stages": [s.serialize() for s in self.inner_stages],
    }

  @classmethod
  def deserialize(cls, data: dict) -> "Stage":
    steps = [Step.deserialize(s) for s in data.get("steps", [])]
    inner_stages = [Stage.deserialize(s) for s in data.get("inner_stages", [])]
    return cls(steps=steps, repeats=data.get("repeats", 1), inner_stages=inner_stages)


@dataclass
class Protocol(SerializableMixin):
  """A complete thermocycler run profile.

  Args:
    stages: Ordered list of stages that constitute the protocol.
    name: Protocol name used for device storage and logging. Empty string
      means unnamed / scratch.
    lid_temperature: Default lid/cover temperature in °C applied to all
      steps unless overridden at the Stage or Step level. None means use
      the device/backend default.
  """

  stages: List[Stage]
  name: str = ""
  lid_temperature: Optional[float] = None

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "stages": [s.serialize() for s in self.stages],
      "name": self.name,
      "lid_temperature": self.lid_temperature,
    }

  @classmethod
  def deserialize(cls, data: dict) -> "Protocol":
    stages = [Stage.deserialize(s) for s in data.get("stages", [])]
    return cls(
      stages=stages,
      name=data.get("name", ""),
      lid_temperature=data.get("lid_temperature"),
    )


class LidStatus(enum.Enum):
  """Temperature status of the thermocycler lid."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"


class BlockStatus(enum.Enum):
  """Temperature status of the thermocycler block."""

  IDLE = "idle"
  HOLDING_AT_TARGET = "holding at target"
