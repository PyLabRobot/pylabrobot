"""Priming the strip washer manifold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Submerge

_PAYLOAD_LENGTH = 12
_DEFINITION_FIELDS = 6


@dataclass
class StripPrime(Step):
  """Pump fluid through the strip washer manifold until its lines are full.

  Attributes:
    volume: Volume to pump in µL. What the manifold accepts depends on which one is fitted.
    flow_rate: How fast to pump.
    cycles: How many prime cycles to run.
    submerge: Whether to leave the tips in fluid afterwards, and for how long.
  """

  step_type: ClassVar[StepType] = StepType.STRIP_PRIME

  volume: int = 5000
  flow_rate: int = 5
  cycles: int = 2
  submerge: Submerge = field(default_factory=Submerge)

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition.
    """
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.volume}|{self.flow_rate}"
      f"|{self.cycles}|{self.submerge.to_definition()}"
    )

  @classmethod
  def from_definition(cls, text: str) -> StripPrime:
    """Read the step back from its definition text.

    This layout keeps empty fields rather than dropping them, so an empty field still holds its
    place.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout.
    """
    volume, flow_rate, cycles, submerge, duration = definition.own_fields(
      text,
      cls.step_type,
      _DEFINITION_FIELDS,
      empty_ok=True,
      defaults=cls.default_definition,
    )
    return cls(
      volume=definition.number(volume, 16),
      flow_rate=definition.number(flow_rate, 8),
      cycles=definition.number(cycles, 8),
      submerge=Submerge.from_definition(submerge, duration),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    return pad(
      u16(self.volume) + u8(self.flow_rate) + u8(self.cycles) + u16(self.submerge.wire_minutes),
      _PAYLOAD_LENGTH,
    )
