"""Running the manifold's automatic cleaning cycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.buffer import Buffer
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.durations import (
  format_hours_minutes,
  parse_hours_minutes,
)

_PAYLOAD_LENGTH = 7
_DEFINITION_FIELDS = 3


@dataclass
class ManifoldAutoClean(Step):
  """Soak the manifold in cleaning fluid for a set time.

  Attributes:
    buffer: Which buffer inlet the cleaning fluid comes from.
    duration: How long to clean, in seconds. The instrument runs this at whole-minute resolution.
  """

  step_type: ClassVar[StepType] = StepType.MANIFOLD_AUTO_CLEAN

  buffer: Buffer = "A"
  duration: int = 3600

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition.
    """
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.buffer}"
      f"|{format_hours_minutes(self.duration)}"
    )

  @classmethod
  def from_definition(cls, text: str) -> ManifoldAutoClean:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a buffer that
        does not exist.
    """
    buffer, duration = definition.own_fields(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    if buffer not in definition.BUFFERS:
      raise ValueError(f"unknown buffer: {buffer!r}")
    return cls(buffer=definition.BUFFERS[buffer], duration=parse_hours_minutes(duration))

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    return pad(u8(ord(self.buffer)) + u16(self.duration // 60), _PAYLOAD_LENGTH)
