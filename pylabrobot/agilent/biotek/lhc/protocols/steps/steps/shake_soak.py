"""Shaking and soaking the plate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_axis import SHAKE_AXIS_TO_BYTE
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_intensity import SHAKE_INTENSITY_TO_BYTE
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Shake, Soak

_PAYLOAD_LENGTH = 11
_DEFINITION_FIELDS = 8


@dataclass
class ShakeSoak(Step):
  """Shake the plate, stand still, or both.

  The wash types reuse this step for the pause after each dispense, and switch it off through
  ``enabled`` when that stage does not run. A step a caller builds is always enabled.

  Attributes:
    enabled: Whether the step does anything at all.
    move_carrier_home: Whether the carrier returns home afterwards. Required when shaking and
      soaking together take longer than a minute.
    shake: Whether to shake, for how long, along which axis and how vigorously.
    soak: Whether to soak, and for how long.
  """

  step_type: ClassVar[StepType] = StepType.SHAKE_SOAK

  enabled: bool = True
  move_carrier_home: bool = True
  shake: Shake = field(default_factory=Shake)
  soak: Soak = field(default_factory=Soak)

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    ``enabled`` is not stored: a step read back from a protocol file is always enabled, and a wash
    supplies the flag from its own stage selection.

    Returns:
      The ``|``-separated definition.
    """
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.move_carrier_home}"
      f"|{self.shake.to_definition()}|{self.soak.to_definition()}"
    )

  @classmethod
  def from_definition(cls, text: str) -> ShakeSoak:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout.
    """
    (
      move_carrier_home,
      shake_enabled,
      shake_duration,
      axis,
      intensity,
      soak_enabled,
      soak_duration,
    ) = definition.own_fields(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    return cls(
      move_carrier_home=definition.flag(move_carrier_home),
      shake=Shake.from_definition(shake_enabled, shake_duration, axis, intensity),
      soak=Soak.from_definition(soak_enabled, soak_duration),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    A disabled step keeps its length and sends zeroes, so it runs as a no-op.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    return pad(
      u8(1 if (self.move_carrier_home and self.enabled) else 0)
      + u16(self.shake.wire_duration if self.enabled else 0)
      + u8(SHAKE_INTENSITY_TO_BYTE[self.shake.intensity])
      + u8(SHAKE_AXIS_TO_BYTE[self.shake.axis])
      + u16(self.soak.wire_duration if self.enabled else 0),
      _PAYLOAD_LENGTH,
    )
