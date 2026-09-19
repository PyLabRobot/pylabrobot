"""Washing through the strip washer manifold."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.wash_format import WASH_FORMAT_TO_BYTE, WashFormat
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import WashStages
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.shake_soak import ShakeSoak
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_aspirate import StripAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_dispense import StripDispense

_PAYLOAD_LENGTH = 108
_DEFINITION_FIELDS = 10
_DEFINITION_PARTS = 6
_PART_SEPARATOR = "#"


@dataclass
class StripWash(Step):
  """Wash a plate through the strip washer manifold.

  Built like a manifold wash, with two differences: well selection belongs to the wash rather than
  to its steps, and there are no sectors -- the selections do that job.

  Attributes:
    wash_format: Whether to wash the whole plate, selected sectors or selected strips.
    cycles: How many wash cycles to run.
    stages: Which optional stages run.
    columns: Which columns to wash.
    rows: Which rows to wash.
    bottom_wash: The dispense that washes the bottom of the well.
    aspirate: The aspirate that empties the well at the start of each cycle.
    dispense: The dispense that refills the well.
    shake_soak: The pause after each dispense.
    final_aspirate: The aspirate that empties the well after the last cycle.
  """

  step_type: ClassVar[StepType] = StepType.STRIP_WASH

  wash_format: WashFormat = "Plate"
  cycles: int = 3
  stages: WashStages = field(default_factory=WashStages)
  columns: WellMask = field(default_factory=WellMask.all_columns)
  rows: WellMask = field(default_factory=WellMask.all_rows)
  bottom_wash: StripDispense = field(default_factory=lambda: StripDispense(in_wash=True))
  aspirate: StripAspirate = field(default_factory=lambda: StripAspirate(in_wash=True))
  dispense: StripDispense = field(default_factory=lambda: StripDispense(in_wash=True))
  shake_soak: ShakeSoak = field(default_factory=ShakeSoak)
  final_aspirate: StripAspirate = field(default_factory=lambda: StripAspirate(in_wash=True))

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The definition, its six parts joined by ``#``.
    """
    head = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.wash_format}|{self.cycles}"
      f"|{self.stages.to_definition()}|{self.columns.to_definition()}"
      f"|{self.rows.to_definition()}"
    )
    return _PART_SEPARATOR.join(
      [
        head,
        self.bottom_wash.to_definition(),
        self.aspirate.to_definition(),
        self.dispense.to_definition(),
        self.shake_soak.to_definition(),
        self.final_aspirate.to_definition(),
      ]
    )

  @classmethod
  def from_definition(cls, text: str) -> StripWash:
    """Read the step back from its definition text.

    Args:
      text: The definition, its six parts joined by ``#``.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have six parts, or a part does not have its step
        type's layout, or the format is not one of the three.
    """
    parts = text.split(_PART_SEPARATOR)
    if len(parts) != _DEFINITION_PARTS:
      raise ValueError(f"{cls.step_type.name} expects {_DEFINITION_PARTS} parts, got {len(parts)}")
    head, bottom_wash, aspirate, dispense, shake_soak, final_aspirate = parts
    own = definition.own_fields(
      head, cls.step_type, _DEFINITION_FIELDS, empty_ok=True, defaults=cls.default_definition
    )
    wash_format, cycles = own[0], own[1]
    if wash_format not in definition.WASH_FORMATS:
      raise ValueError(f"unknown wash format: {wash_format!r}")
    return cls(
      wash_format=definition.WASH_FORMATS[wash_format],
      cycles=definition.number(cycles, 8),
      stages=WashStages.from_definition(*own[2:7]),
      columns=WellMask.from_definition(own[7]),
      rows=WellMask.from_definition(own[8]),
      bottom_wash=StripDispense.from_definition(bottom_wash),
      aspirate=StripAspirate.from_definition(aspirate),
      dispense=StripDispense.from_definition(dispense),
      shake_soak=ShakeSoak.from_definition(shake_soak),
      final_aspirate=StripAspirate.from_definition(final_aspirate),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    The five steps are sent with the final aspirate second rather than last, and the wash's own
    selections come after them. The two dispenses take their pre-dispense from different stage
    flags: the bottom wash from the one before washing, the cycle dispense from the one between
    cycles.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    bottom_wash = dataclasses.replace(
      self.bottom_wash,
      in_wash=True,
      force_pre_dispense=False,
      pre_dispense=dataclasses.replace(
        self.bottom_wash.pre_dispense, enabled=self.stages.pre_dispense_before
      ),
    )
    dispense = dataclasses.replace(
      self.dispense,
      in_wash=True,
      force_pre_dispense=self.stages.pre_dispense_between,
      pre_dispense=dataclasses.replace(self.dispense.pre_dispense, enabled=False),
    )
    aspirate = dataclasses.replace(self.aspirate, in_wash=True)
    final_aspirate = dataclasses.replace(self.final_aspirate, in_wash=True)
    shake_soak = dataclasses.replace(self.shake_soak, enabled=self.stages.shake_soak_after_dispense)
    return pad(
      u8(1 if self.stages.bottom_wash else 0)
      + u8(1 if self.stages.final_aspirate else 0)
      + u8(WASH_FORMAT_TO_BYTE[self.wash_format])
      + u8(self.cycles)
      + bottom_wash.to_bytes(settings)
      + final_aspirate.to_bytes(settings)
      + aspirate.to_bytes(settings)
      + dispense.to_bytes(settings)
      + shake_soak.to_bytes(settings)
      + self.columns.to_bytes()
      + self.rows.to_bytes(),
      _PAYLOAD_LENGTH,
    )
