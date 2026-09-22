"""Priming the wash manifold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.buffer import Buffer
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Submerge

_PAYLOAD_LENGTH = 12
_DEFINITION_FIELDS = 8


@dataclass
class ManifoldPrime(Step):
  """Pump fluid through the wash manifold until the lines are full.

  Attributes:
    buffer: Which buffer inlet to draw from.
    volume: Volume to pump in µL. Stored at millilitre resolution.
    flow_rate: How fast to pump.
    prime_low_flow_path: Whether to prime the low flow path as well.
    low_flow_path_volume: Volume to pump through the low flow path in µL, when it is primed.
      Stored at millilitre resolution.
    submerge: Whether to leave the tips in fluid afterwards, and for how long.
  """

  step_type: ClassVar[StepType] = StepType.MANIFOLD_PRIME

  buffer: Buffer = "A"
  volume: int = 40_000
  flow_rate: int = 9
  prime_low_flow_path: bool = True
  low_flow_path_volume: int = 5_000
  submerge: Submerge = field(default_factory=Submerge)

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition.
    """
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.buffer}|{self.volume // 1000}"
      f"|{self.flow_rate}|{self.prime_low_flow_path}|{self.low_flow_path_volume // 1000}"
      f"|{self.submerge.to_definition()}"
    )

  @classmethod
  def from_definition(cls, text: str) -> ManifoldPrime:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout.
    """
    (
      buffer,
      volume,
      flow_rate,
      low_flow,
      low_flow_volume,
      submerge,
      duration,
    ) = definition.own_fields(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    if buffer not in definition.BUFFERS:
      raise ValueError(f"unknown buffer: {buffer!r}")
    return cls(
      buffer=definition.BUFFERS[buffer],
      volume=definition.number(volume, 16) * 1000,
      flow_rate=definition.number(flow_rate, 8),
      prime_low_flow_path=definition.flag(low_flow),
      low_flow_path_volume=definition.number(low_flow_volume, 16) * 1000,
      submerge=Submerge.from_definition(submerge, duration),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    Both optional volumes are sent as zero when they are switched off rather than left out, so the
    payload has one layout.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    low_flow_volume = self.low_flow_path_volume if self.prime_low_flow_path else 0
    return pad(
      u8(ord(self.buffer))
      + u16(self.volume // 1000)
      + u8(self.flow_rate)
      + u16(low_flow_volume // 1000)
      + u16(self.submerge.wire_minutes),
      _PAYLOAD_LENGTH,
    )
