"""Dispensing through the syringe dispenser."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe import SYRINGE_TO_BYTE, Syringe
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe_bottle import (
  SYRINGE_BOTTLE_TO_BYTE,
  SyringeBottle,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import PreDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning

_PAYLOAD_LENGTH = 25
_WIDE_OFFSET_PAYLOAD_LENGTH = 26
_DEFINITION_FIELDS = 13

_BYTE_TO_SYRINGE: dict[int, Syringe] = {value: key for key, value in SYRINGE_TO_BYTE.items()}
_BYTE_TO_SYRINGE_BOTTLE: dict[int, SyringeBottle] = {
  value: key for key, value in SYRINGE_BOTTLE_TO_BYTE.items()
}


@dataclass
class SyringeDispense(Step):
  """Dispense a volume into every selected well from a syringe.

  Attributes:
    syringe: Which syringe to dispense from.
    volume: Volume per well in µL.
    flow_rate: How fast to dispense.
    positioning: Where in the well to dispense.
    pre_dispense: Whether to pre-dispense first, at what volume and how many times.
    pump_delay: How long the pump waits between wells, in ms.
    columns: Which columns to dispense into.
    syringe_bottle: Which bottle to draw from.
    rows: Which rows to dispense into. Only instruments that select rows store this.
    selects_rows: Whether the instrument selects rows at all.
  """

  step_type: ClassVar[StepType] = StepType.SYRINGE_DISPENSE

  syringe: Syringe = "A"
  volume: int = 50
  flow_rate: int = 2
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=333))
  pre_dispense: PreDispense = field(default_factory=lambda: PreDispense(volume=50, count=2))
  pump_delay: int = 0
  columns: WellMask = field(default_factory=WellMask.all_columns)
  syringe_bottle: SyringeBottle = "A1"
  rows: WellMask = field(default_factory=WellMask.all_rows)
  selects_rows: bool = False

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    The pre-dispense flow rate is not stored by this step type.

    Returns:
      The ``|``-separated definition, with the row selection only on an instrument that selects
      rows.
    """
    text = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{SYRINGE_TO_BYTE[self.syringe]}"
      f"|{self.volume}|{self.flow_rate}|{self.positioning.to_definition()}"
      f"|{self.pre_dispense.enabled}|{self.pre_dispense.volume}|{self.pre_dispense.count}"
      f"|{self.pump_delay}|{self.columns.to_definition()}"
      f"|{SYRINGE_BOTTLE_TO_BYTE[self.syringe_bottle]}"
    )
    if self.selects_rows:
      text += f"|{self.rows.to_definition()}"
    return text

  @classmethod
  def from_definition(cls, text: str) -> SyringeDispense:
    """Read the step back from its definition text.

    Whether the row selection is present is what says whether the instrument selects rows.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a syringe or
        bottle that does not exist.
    """
    own = definition.own_fields_at_least(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    (
      syringe,
      volume,
      flow_rate,
      z,
      x,
      y,
      pre_enabled,
      pre_volume,
      pre_count,
      pump_delay,
      columns,
      bottle,
    ) = own[:12]
    tail = own[12:]
    if int(syringe) not in _BYTE_TO_SYRINGE:
      raise ValueError(f"unknown syringe: {syringe!r}")
    if int(bottle) not in _BYTE_TO_SYRINGE_BOTTLE:
      raise ValueError(f"unknown syringe bottle: {bottle!r}")
    return cls(
      syringe=_BYTE_TO_SYRINGE[int(syringe)],
      volume=definition.number(volume, 16),
      flow_rate=definition.number(flow_rate, 8),
      positioning=Positioning(
        z_steps=definition.signed(z, 16),
        x_steps=definition.signed(x, 16),
        y_steps=definition.signed(y, 8),
      ),
      pre_dispense=PreDispense(
        enabled=definition.flag(pre_enabled),
        volume=definition.number(pre_volume, 16),
        count=definition.number(pre_count, 8),
      ),
      pump_delay=definition.number(pump_delay, 16),
      columns=WellMask.from_definition(columns),
      syringe_bottle=_BYTE_TO_SYRINGE_BOTTLE[int(bottle)],
      rows=WellMask.from_definition(tail[0]) if tail else WellMask.all_rows(),
      selects_rows=bool(tail),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    With the wider dispense offsets fitted the X offset takes two bytes instead of one, which
    makes this the one step whose payload changes length. The row selection is sent inverted, so
    a selected row contributes a zero bit.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    if settings.advanced_dispense_offsets:
      offset_x, length = i16(self.positioning.x_steps), _WIDE_OFFSET_PAYLOAD_LENGTH
    else:
      offset_x, length = i8(self.positioning.x_steps), _PAYLOAD_LENGTH
    payload = (
      u8(SYRINGE_TO_BYTE[self.syringe] - 1)
      + u16(self.volume)
      + u8(self.flow_rate)
      + offset_x
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + u16(self.pump_delay)
      + u16(self.pre_dispense.wire_volume)
      + u8(self.pre_dispense.count)
      + self.columns.to_bytes()
      + u8(SYRINGE_BOTTLE_TO_BYTE[self.syringe_bottle] - 1)
    )
    if self.selects_rows:
      payload += self.rows.to_bytes_inverted()
    return pad(payload, length)
