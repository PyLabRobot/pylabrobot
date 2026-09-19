"""Priming a syringe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe import SYRINGE_TO_BYTE, Syringe
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe_bottle import (
  SYRINGE_BOTTLE_TO_BYTE,
  SyringeBottle,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Submerge

_PAYLOAD_LENGTH = 12
_DEFINITION_FIELDS = 10

_BYTE_TO_SYRINGE: dict[int, Syringe] = {value: key for key, value in SYRINGE_TO_BYTE.items()}
_BYTE_TO_SYRINGE_BOTTLE: dict[int, SyringeBottle] = {
  value: key for key, value in SYRINGE_BOTTLE_TO_BYTE.items()
}


@dataclass
class SyringePrime(Step):
  """Draw fluid through a syringe until its lines are full.

  Attributes:
    syringe: Which syringe to prime.
    volume: Volume to draw in µL.
    flow_rate: How fast to draw.
    cycles: How many prime cycles to run.
    pump_delay: How long the pump waits between cycles, in ms.
    reserved_flag: A flag the payload carries that no setting varies. Always set.
    submerge: Whether to leave the tips in fluid afterwards, and for how long.
    syringe_bottle: Which bottle to draw from.
  """

  step_type: ClassVar[StepType] = StepType.SYRINGE_PRIME

  syringe: Syringe = "A"
  volume: int = 5000
  flow_rate: int = 5
  cycles: int = 2
  pump_delay: int = 0
  reserved_flag: bool = True
  submerge: Submerge = field(default_factory=Submerge)
  syringe_bottle: SyringeBottle = "A1"

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition.
    """
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{SYRINGE_TO_BYTE[self.syringe]}"
      f"|{self.volume}|{self.flow_rate}|{self.cycles}|{self.pump_delay}"
      f"|{self.reserved_flag}|{self.submerge.to_definition()}"
      f"|{SYRINGE_BOTTLE_TO_BYTE[self.syringe_bottle]}"
    )

  @classmethod
  def from_definition(cls, text: str) -> SyringePrime:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a syringe or
        bottle that does not exist.
    """
    (
      syringe,
      volume,
      flow_rate,
      cycles,
      pump_delay,
      reserved_flag,
      submerge,
      duration,
      bottle,
    ) = definition.own_fields(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    if int(syringe) not in _BYTE_TO_SYRINGE:
      raise ValueError(f"unknown syringe: {syringe!r}")
    if int(bottle) not in _BYTE_TO_SYRINGE_BOTTLE:
      raise ValueError(f"unknown syringe bottle: {bottle!r}")
    return cls(
      syringe=_BYTE_TO_SYRINGE[int(syringe)],
      volume=definition.number(volume, 16),
      flow_rate=definition.number(flow_rate, 8),
      cycles=definition.number(cycles, 8),
      pump_delay=definition.number(pump_delay, 16),
      reserved_flag=definition.flag(reserved_flag),
      submerge=Submerge.from_definition(submerge, duration),
      syringe_bottle=_BYTE_TO_SYRINGE_BOTTLE[int(bottle)],
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    Both selections are sent one lower than they are stored, so the first syringe and the first
    bottle are zero.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    return pad(
      u8(SYRINGE_TO_BYTE[self.syringe] - 1)
      + u16(self.volume)
      + u8(self.flow_rate)
      + u8(self.cycles)
      + u16(self.pump_delay)
      + u8(1 if self.reserved_flag else 0)
      + u16(self.submerge.wire_minutes)
      + u8(SYRINGE_BOTTLE_TO_BYTE[self.syringe_bottle] - 1),
      _PAYLOAD_LENGTH,
    )
