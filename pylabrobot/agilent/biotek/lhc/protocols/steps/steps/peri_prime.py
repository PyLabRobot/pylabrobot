"""Priming a peristaltic pump."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_type import (
  CASSETTE_TYPE_TO_BYTE,
  CassetteType,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_flow_rate import (
  PERI_FLOW_RATE_TO_BYTE,
  PeriFlowRate,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PERI_PUMP_TO_BYTE, PeriPump
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import RandomAccess

_PAYLOAD_LENGTH = 10
_DEFINITION_FIELDS = 8

_NO_CASSETTE_REQUIREMENT = 255
_NO_PUMP = 0

_BYTE_TO_CASSETTE_TYPE: dict[int, CassetteType] = {
  value: key for key, value in CASSETTE_TYPE_TO_BYTE.items()
}
_BYTE_TO_PERI_PUMP: dict[int, PeriPump] = {value: key for key, value in PERI_PUMP_TO_BYTE.items()}
_PERI_FLOW_RATES: dict[str, PeriFlowRate] = {"Low": "Low", "Medium": "Medium", "High": "High"}


@dataclass
class PeriPrime(Step):
  """Pump fluid through a peristaltic cassette until its tubing is full.

  Either a volume or a duration drives the step, and both are stored whichever is in use.

  Attributes:
    fixed_volume: Whether the step runs to a volume rather than to a duration.
    volume: Volume per tube in µL, used when the step runs to a volume.
    duration: How long to pump in seconds, used when the step runs to a duration.
    flow_rate: How fast to pump.
    home_when_finished: Whether the carrier homes after the step.
    cassette_type: The cassette the step requires, or None to accept whatever is fitted.
    peri_pump: Which pump to drive, or None to leave the choice to the instrument.
    random_access: Whether the step dispenses at random access, and with which head.
  """

  step_type: ClassVar[StepType] = StepType.PERI_PRIME

  fixed_volume: bool = True
  volume: int = 300
  duration: int = 3
  flow_rate: PeriFlowRate = "High"
  home_when_finished: bool = True
  cassette_type: CassetteType | None = "Any"
  peri_pump: PeriPump | None = "Primary"
  random_access: RandomAccess = field(default_factory=RandomAccess)

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    The random-access fields are written only when the step uses random access. A model old
    enough not to read them cannot run such a step at all, which is validation's to reject rather
    than something to paper over here by dropping the fields.

    Returns:
      The ``|``-separated definition.
    """
    cassette = (
      _NO_CASSETTE_REQUIREMENT
      if self.cassette_type is None
      else CASSETTE_TYPE_TO_BYTE[self.cassette_type]
    )
    pump = _NO_PUMP if self.peri_pump is None else PERI_PUMP_TO_BYTE[self.peri_pump]
    text = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.fixed_volume}|{self.volume}"
      f"|{self.duration}|{self.flow_rate}|{self.home_when_finished}|{cassette}|{pump}"
    )
    if self.random_access.enabled:
      text += f"|{self.random_access.to_definition()}"
    return text

  @classmethod
  def from_definition(cls, text: str) -> PeriPrime:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a flow rate,
        cassette or pump that does not exist.
    """
    own = definition.own_fields_at_least(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    fixed_volume, volume, duration, flow_rate, home, cassette, pump = own[:7]
    tail = own[7:]
    if flow_rate not in _PERI_FLOW_RATES:
      raise ValueError(f"unknown peristaltic flow rate: {flow_rate!r}")
    if int(cassette) != _NO_CASSETTE_REQUIREMENT and int(cassette) not in _BYTE_TO_CASSETTE_TYPE:
      raise ValueError(f"unknown cassette type: {cassette!r}")
    if int(pump) != _NO_PUMP and int(pump) not in _BYTE_TO_PERI_PUMP:
      raise ValueError(f"unknown peristaltic pump: {pump!r}")
    return cls(
      fixed_volume=definition.flag(fixed_volume),
      volume=definition.number(volume, 16),
      duration=definition.number(duration, 16),
      flow_rate=_PERI_FLOW_RATES[flow_rate],
      home_when_finished=definition.flag(home),
      cassette_type=_BYTE_TO_CASSETTE_TYPE.get(int(cassette)),
      peri_pump=_BYTE_TO_PERI_PUMP.get(int(pump)),
      random_access=RandomAccess.from_definition(*tail) if tail else RandomAccess(),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    Whichever of volume and duration does not drive the step is sent as zero.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    cassette = (
      _NO_CASSETTE_REQUIREMENT
      if self.cassette_type is None
      else CASSETTE_TYPE_TO_BYTE[self.cassette_type]
    )
    pump = _NO_PUMP if self.peri_pump is None else PERI_PUMP_TO_BYTE[self.peri_pump]
    return pad(
      u16(self.volume if self.fixed_volume else 0)
      + u16(0 if self.fixed_volume else self.duration)
      + u8(PERI_FLOW_RATE_TO_BYTE[self.flow_rate])
      + u8(1 if self.home_when_finished else 0)
      + u8(cassette)
      + u8(pump),
      _PAYLOAD_LENGTH,
    )
