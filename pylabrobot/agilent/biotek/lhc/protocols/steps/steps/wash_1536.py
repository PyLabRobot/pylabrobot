"""Washing a 1536-well plate."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.travel_rate import TRAVEL_RATE_TO_BYTE
from pylabrobot.agilent.biotek.lhc.enums.steps.wash_format import WASH_FORMAT_TO_BYTE, WashFormat
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import PreDispense, WashStages
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.shake_soak import ShakeSoak
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_dispense import SyringeDispense

_PAYLOAD_LENGTH = 67
_DEFINITION_FIELDS = 9
_DEFINITION_PARTS = 5
_PART_SEPARATOR = "#"


@dataclass
class Wash1536(Step):
  """Wash a 1536-well plate, dispensing from a syringe rather than from the wash manifold.

  There is no bottom wash, and well selection lives on the syringe dispense. The volume and count
  used to pre-dispense before washing belong to the wash itself rather than to one of its steps.

  Attributes:
    wash_format: Whether to wash the whole plate, selected sectors or selected strips.
    cycles: How many wash cycles to run.
    stages: Which optional stages run. The bottom wash flag is unused here.
    pre_dispense_before_volume: Volume per well in µL to pre-dispense before washing starts.
    pre_dispense_before_count: How many times to pre-dispense before washing starts.
    aspirate: The aspirate that empties the well at the start of each cycle.
    dispense: The syringe dispense that refills the well.
    shake_soak: The pause after each dispense.
    final_aspirate: The aspirate that empties the well after the last cycle.
  """

  step_type: ClassVar[StepType] = StepType.WASH_1536

  wash_format: WashFormat = "Plate"
  cycles: int = 3
  stages: WashStages = field(default_factory=WashStages)
  pre_dispense_before_volume: int = 10
  pre_dispense_before_count: int = 2
  aspirate: ManifoldAspirate = field(default_factory=lambda: ManifoldAspirate(in_wash=True))
  dispense: SyringeDispense = field(
    default_factory=lambda: SyringeDispense(
      pre_dispense=PreDispense(enabled=True, volume=50, count=2)
    )
  )
  shake_soak: ShakeSoak = field(default_factory=ShakeSoak)
  final_aspirate: ManifoldAspirate = field(default_factory=lambda: ManifoldAspirate(in_wash=True))

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    The stage flags are not contiguous here: the volume and count used before washing sit between
    the first two.

    Returns:
      The definition, its five parts joined by ``#``.
    """
    head = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.wash_format}|{self.cycles}"
      f"|{self.stages.pre_dispense_before}|{self.pre_dispense_before_volume}"
      f"|{self.pre_dispense_before_count}|{self.stages.pre_dispense_between}"
      f"|{self.stages.final_aspirate}|{self.stages.shake_soak_after_dispense}"
    )
    return _PART_SEPARATOR.join(
      [
        head,
        self.aspirate.to_definition(),
        self.dispense.to_definition(),
        self.shake_soak.to_definition(),
        self.final_aspirate.to_definition(),
      ]
    )

  @classmethod
  def from_definition(cls, text: str) -> Wash1536:
    """Read the step back from its definition text.

    Args:
      text: The definition, its five parts joined by ``#``.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have five parts, or a part does not have its step
        type's layout, or the format is not one of the three.
    """
    parts = [part for part in text.split(_PART_SEPARATOR) if part]
    if len(parts) != _DEFINITION_PARTS:
      raise ValueError(f"{cls.step_type.name} expects {_DEFINITION_PARTS} parts, got {len(parts)}")
    head, aspirate, dispense, shake_soak, final_aspirate = parts
    (
      wash_format,
      cycles,
      before,
      before_volume,
      before_count,
      between,
      final,
      shake_after,
    ) = definition.own_fields(
      head, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    if wash_format not in definition.WASH_FORMATS:
      raise ValueError(f"unknown wash format: {wash_format!r}")
    return cls(
      wash_format=definition.WASH_FORMATS[wash_format],
      cycles=definition.number(cycles, 8),
      stages=WashStages(
        pre_dispense_before=definition.flag(before),
        pre_dispense_between=definition.flag(between),
        final_aspirate=definition.flag(final),
        shake_soak_after_dispense=definition.flag(shake_after),
      ),
      pre_dispense_before_volume=definition.number(before_volume, 16),
      pre_dispense_before_count=definition.number(before_count, 8),
      aspirate=ManifoldAspirate.from_definition(aspirate),
      dispense=SyringeDispense.from_definition(dispense),
      shake_soak=ShakeSoak.from_definition(shake_soak),
      final_aspirate=ManifoldAspirate.from_definition(final_aspirate),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    The two aspirates contribute seven bytes each -- delay, travel rate and the three offsets --
    rather than their whole payload, so nothing else they carry is sent. The payload grows by a
    byte when the syringe dispense sends a wider X offset.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    shake_soak = dataclasses.replace(self.shake_soak, enabled=self.stages.shake_soak_after_dispense)
    length = _PAYLOAD_LENGTH + (1 if settings.advanced_dispense_offsets else 0)
    return pad(
      u8(1 if self.stages.pre_dispense_before else 0)
      + u8(1 if self.stages.shake_soak_after_dispense else 0)
      + u8(1 if self.stages.pre_dispense_between else 0)
      + u8(1 if self.stages.final_aspirate else 0)
      + u16(self.pre_dispense_before_volume)
      + u8(self.pre_dispense_before_count)
      + u8(WASH_FORMAT_TO_BYTE[self.wash_format])
      + u8(self.cycles)
      + _aspirate_extract(self.aspirate)
      + _aspirate_extract(self.final_aspirate)
      + self.dispense.to_bytes(settings)
      + shake_soak.to_bytes(settings),
      length,
    )


def _aspirate_extract(aspirate: ManifoldAspirate) -> bytes:
  """The seven bytes of an aspirate that this wash sends.

  Args:
    aspirate: The aspirate to take them from.

  Returns:
    Its delay, travel rate and offsets, in the order the payload carries them.
  """
  return (
    u16(aspirate.delay)
    + u8(TRAVEL_RATE_TO_BYTE[aspirate.travel_rate])
    + i8(aspirate.positioning.x_steps)
    + i8(aspirate.positioning.y_steps)
    + i16(aspirate.positioning.z_steps)
  )
