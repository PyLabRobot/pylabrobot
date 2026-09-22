"""Washing through the wash manifold."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.wash_format import WASH_FORMAT_TO_BYTE, WashFormat
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Sectors, WashStages
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_dispense import ManifoldDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.shake_soak import ShakeSoak

_PAYLOAD_LENGTH = 101
_DEFINITION_FIELDS = 9
_DEFINITION_PARTS = 6
_PART_SEPARATOR = "#"


@dataclass
class ManifoldWash(Step):
  """Wash a plate: dispense, aspirate and the optional stages around them, repeated.

  A wash carries five complete steps of its own, each stored as a full definition of its own type.
  Two of them do double duty and are named for what they are rather than for the stage that
  switches them on: the bottom wash carries the pre-dispense used before washing starts, and the
  dispense carries the one used between cycles.

  Attributes:
    wash_format: Whether to wash the whole plate, selected sectors or selected strips.
    sectors: Which sectors to wash, when the format selects sectors.
    cycles: How many wash cycles to run.
    stages: Which optional stages run.
    bottom_wash: The dispense that washes the bottom of the well.
    aspirate: The aspirate that empties the well at the start of each cycle.
    dispense: The dispense that refills the well.
    shake_soak: The pause after each dispense.
    final_aspirate: The aspirate that empties the well after the last cycle.
  """

  step_type: ClassVar[StepType] = StepType.MANIFOLD_WASH

  wash_format: WashFormat = "Plate"
  sectors: Sectors = field(default_factory=Sectors)
  cycles: int = 3
  stages: WashStages = field(default_factory=WashStages)
  bottom_wash: ManifoldDispense = field(default_factory=ManifoldDispense)
  aspirate: ManifoldAspirate = field(default_factory=lambda: ManifoldAspirate(in_wash=True))
  dispense: ManifoldDispense = field(default_factory=ManifoldDispense)
  shake_soak: ShakeSoak = field(default_factory=ShakeSoak)
  final_aspirate: ManifoldAspirate = field(default_factory=lambda: ManifoldAspirate(in_wash=True))

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    The wash's own fields come first, then its five steps, all joined by ``#``.

    Returns:
      The definition.
    """
    head = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.wash_format}"
      f"|{self.sectors.to_definition()}|{self.cycles}|{self.stages.to_definition()}"
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
  def from_definition(cls, text: str) -> ManifoldWash:
    """Read the step back from its definition text.

    Args:
      text: The definition, its six parts joined by ``#``.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have six parts, or a part does not have its step
        type's layout, or the format is not one of the three.
    """
    parts = [part for part in text.split(_PART_SEPARATOR) if part]
    if len(parts) != _DEFINITION_PARTS:
      raise ValueError(f"{cls.step_type.name} expects {_DEFINITION_PARTS} parts, got {len(parts)}")
    head, bottom_wash, aspirate, dispense, shake_soak, final_aspirate = parts
    wash_format, sectors, cycles, *stages = definition.own_fields(
      head,
      cls.step_type,
      _DEFINITION_FIELDS,
      defaults=cls.default_definition,
    )
    if wash_format not in definition.WASH_FORMATS:
      raise ValueError(f"unknown wash format: {wash_format!r}")
    return cls(
      wash_format=definition.WASH_FORMATS[wash_format],
      sectors=Sectors.from_definition(sectors),
      cycles=definition.number(cycles, 8),
      stages=WashStages.from_definition(*stages),
      bottom_wash=ManifoldDispense.from_definition(bottom_wash),
      aspirate=ManifoldAspirate.from_definition(aspirate),
      dispense=ManifoldDispense.from_definition(dispense),
      shake_soak=ShakeSoak.from_definition(shake_soak),
      final_aspirate=ManifoldAspirate.from_definition(final_aspirate),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    The five steps are sent in a different order from the one they are stored in: the final
    aspirate comes second here and last there. Three of the wash's stage flags reach into its
    steps, switching off a pre-dispense or the pause however those steps are configured.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    bottom_wash = dataclasses.replace(
      self.bottom_wash,
      pre_dispense=dataclasses.replace(
        self.bottom_wash.pre_dispense,
        enabled=self.bottom_wash.pre_dispense.enabled and self.stages.pre_dispense_before,
      ),
    )
    dispense = dataclasses.replace(
      self.dispense,
      pre_dispense=dataclasses.replace(
        self.dispense.pre_dispense,
        enabled=self.dispense.pre_dispense.enabled and self.stages.pre_dispense_between,
      ),
    )
    aspirate = dataclasses.replace(self.aspirate, in_wash=True)
    final_aspirate = dataclasses.replace(self.final_aspirate, in_wash=True)
    shake_soak = dataclasses.replace(self.shake_soak, enabled=self.stages.shake_soak_after_dispense)
    return pad(
      u8(1 if self.stages.bottom_wash else 0)
      + u8(1 if self.stages.final_aspirate else 0)
      + u8(WASH_FORMAT_TO_BYTE[self.wash_format])
      + u16(self.sectors.value)
      + u8(self.cycles)
      + bottom_wash.to_bytes(settings)
      + final_aspirate.to_bytes(settings)
      + aspirate.to_bytes(settings)
      + dispense.to_bytes(settings)
      + shake_soak.to_bytes(settings),
      _PAYLOAD_LENGTH,
    )
