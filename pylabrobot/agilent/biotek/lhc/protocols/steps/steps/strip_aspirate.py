"""Aspirating through the strip washer manifold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.secondary_aspirate_pattern import (
  SECONDARY_ASPIRATE_PATTERN_TO_BYTE,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.travel_rate import TRAVEL_RATE_TO_BYTE, TravelRate
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import SecondaryAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning

_PAYLOAD_LENGTH = 27
_IN_WASH_PAYLOAD_LENGTH = 20
_DEFINITION_FIELDS = 10


@dataclass
class StripAspirate(Step):
  """Draw the wells empty through the strip washer manifold.

  The strip washer reaches further than the plate washer in every axis and offers two travel rates
  the plate washer does not. It has no vacuum filtration.

  Attributes:
    travel_rate: How fast the tips descend into the well.
    delay: How long to keep aspirating once the tips are down, in ms.
    positioning: Where in the well to aspirate.
    secondary: Whether to aspirate a second time, in what pattern and where.
    columns: Which columns to aspirate. Only sent by a step that stands on its own.
    rows: Which rows to aspirate. Only sent by a step that stands on its own.
    in_wash: Whether the step belongs to a wash, which selects wells itself. Such a step stores
      and sends no selections, and its payload is seven bytes shorter.
  """

  step_type: ClassVar[StepType] = StepType.STRIP_ASPIRATE

  travel_rate: TravelRate = "3"
  delay: int = 0
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=22))
  secondary: SecondaryAspirate = field(default_factory=SecondaryAspirate)
  columns: WellMask = field(default_factory=WellMask.all_columns)
  rows: WellMask = field(default_factory=WellMask.all_rows)
  in_wash: bool = False

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition, with the selections only when the step stands alone.
    """
    text = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.travel_rate}|{self.delay}"
      f"|{self.positioning.to_definition()}|{self.secondary.to_definition()}"
    )
    if not self.in_wash:
      text += f"|{self.columns.to_definition()}|{self.rows.to_definition()}"
    return text

  @classmethod
  def from_definition(cls, text: str) -> StripAspirate:
    """Read the step back from its definition text.

    Whether the selections are present is what says whether the step belongs to a wash. This
    layout keeps empty fields rather than dropping them.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a travel rate
        that does not exist.
    """
    own = definition.own_fields_at_least(
      text, cls.step_type, _DEFINITION_FIELDS, empty_ok=True, defaults=cls.default_definition
    )
    travel_rate, delay, z, x, y, pattern, secondary_z, secondary_x, secondary_y = own[:9]
    masks = own[9:]
    if travel_rate not in definition.TRAVEL_RATES:
      raise ValueError(f"unknown travel rate: {travel_rate!r}")
    return cls(
      travel_rate=definition.TRAVEL_RATES[travel_rate],
      delay=definition.number(delay, 16),
      positioning=Positioning(
        z_steps=definition.signed(z, 16),
        x_steps=definition.signed(x, 16),
        y_steps=definition.signed(y, 8),
      ),
      secondary=SecondaryAspirate.from_definition(pattern, secondary_z, secondary_x, secondary_y),
      columns=WellMask.from_definition(masks[0]) if masks else WellMask.all_columns(),
      rows=WellMask.from_definition(masks[1]) if len(masks) > 1 else WellMask.all_rows(),
      in_wash=not masks,
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    Both offset groups are sent X, Y, Z, the reverse of the order they are stored in, with X two
    bytes wide. The selections are packed one bit per entry, not sampled down.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    payload = (
      u16(self.delay)
      + u8(TRAVEL_RATE_TO_BYTE[self.travel_rate])
      + i16(self.positioning.x_steps)
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + u8(SECONDARY_ASPIRATE_PATTERN_TO_BYTE[self.secondary.pattern])
      + i16(self.secondary.positioning.x_steps)
      + i8(self.secondary.positioning.y_steps)
      + i16(self.secondary.positioning.z_steps)
    )
    if self.in_wash:
      return pad(payload, _IN_WASH_PAYLOAD_LENGTH)
    return pad(payload + self.columns.to_bytes() + self.rows.to_bytes(), _PAYLOAD_LENGTH)
