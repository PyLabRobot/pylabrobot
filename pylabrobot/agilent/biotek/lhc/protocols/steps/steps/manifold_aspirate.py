"""Aspirating through the wash manifold."""

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

_PAYLOAD_LENGTH = 21
_DEFINITION_FIELDS = 12


@dataclass
class ManifoldAspirate(Step):
  """Draw the wells empty through the wash manifold.

  Attributes:
    vacuum_filtration: Whether to pull the wells through a filter plate instead of aspirating
      from above. Not available on a 1536-well plate.
    travel_rate: How fast the tips descend into the well.
    delay: How long to keep aspirating once the tips are down, in ms. Under vacuum filtration this
      is the filtration time in seconds instead.
    positioning: Where in the well to aspirate.
    secondary: Whether to aspirate a second time, in what pattern and where.
    radius: A field the protocol file carries but the instrument is never sent; the payload holds
      zero in its place.
    columns: Which columns to aspirate. Only sent by a step that stands on its own.
    in_wash: Whether the step belongs to a wash, which selects wells itself. Such a step stores
      and sends no column selection.
  """

  step_type: ClassVar[StepType] = StepType.MANIFOLD_ASPIRATE

  vacuum_filtration: bool = False
  travel_rate: TravelRate = "3"
  delay: int = 0
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=22))
  secondary: SecondaryAspirate = field(default_factory=SecondaryAspirate)
  radius: str = "0"
  columns: WellMask = field(default_factory=WellMask.all_columns)
  in_wash: bool = False

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition, with the column selection only when the step stands alone.
    """
    text = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.vacuum_filtration}"
      f"|{self.travel_rate}|{self.delay}|{self.positioning.to_definition()}"
      f"|{self.secondary.to_definition()}|{self.radius}"
    )
    if not self.in_wash:
      text += f"|{self.columns.to_definition()}"
    return text

  @classmethod
  def from_definition(cls, text: str) -> ManifoldAspirate:
    """Read the step back from its definition text.

    Whether the column selection is present is what says whether the step belongs to a wash.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a travel rate
        that does not exist.
    """
    own = definition.own_fields_at_least(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    (
      vacuum,
      travel_rate,
      delay,
      z,
      x,
      y,
      pattern,
      secondary_z,
      secondary_x,
      secondary_y,
      radius,
    ) = own[:11]
    columns = own[11:]
    if travel_rate not in definition.TRAVEL_RATES:
      raise ValueError(f"unknown travel rate: {travel_rate!r}")
    return cls(
      vacuum_filtration=definition.flag(vacuum),
      travel_rate=definition.TRAVEL_RATES[travel_rate],
      delay=definition.number(delay, 16),
      positioning=Positioning(
        z_steps=definition.signed(z, 16),
        x_steps=definition.signed(x, 8),
        y_steps=definition.signed(y, 8),
      ),
      secondary=SecondaryAspirate.from_definition(pattern, secondary_z, secondary_x, secondary_y),
      radius=radius,
      columns=WellMask.from_definition(columns[0]) if columns else WellMask.all_columns(),
      in_wash=not columns,
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    Both offset groups are sent X, Y, Z, the reverse of the order they are stored in. A step
    belonging to a wash sends no columns.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    columns = 0 if self.in_wash else self.columns.to_bits()
    return pad(
      u8(1 if self.vacuum_filtration else 0)
      + u16(self.delay)
      + u8(TRAVEL_RATE_TO_BYTE[self.travel_rate])
      + i8(self.positioning.x_steps)
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + u8(SECONDARY_ASPIRATE_PATTERN_TO_BYTE[self.secondary.pattern])
      + i8(self.secondary.positioning.x_steps)
      + i8(self.secondary.positioning.y_steps)
      + i16(self.secondary.positioning.z_steps)
      + u16(0)
      + u16(columns),
      _PAYLOAD_LENGTH,
    )
