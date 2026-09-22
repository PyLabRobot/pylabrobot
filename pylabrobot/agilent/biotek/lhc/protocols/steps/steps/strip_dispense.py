"""Dispensing through the strip washer manifold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import (
  PreDispense,
  VacuumDelay,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning

_PAYLOAD_LENGTH = 27
_IN_WASH_PAYLOAD_LENGTH = 20
_DEFINITION_FIELDS = 12


@dataclass
class StripDispense(Step):
  """Dispense a volume into every selected well through the strip washer manifold.

  The strip washer has no valve selection, so there is no buffer to choose.

  Attributes:
    volume: Volume per well in µL.
    flow_rate: How fast to dispense.
    positioning: Where in the well to dispense.
    pre_dispense: Whether to pre-dispense first, at what volume, rate and how many times.
    vacuum: Whether to hold the vacuum off until a volume has been dispensed.
    columns: Which columns to dispense into. Only sent by a step that stands on its own.
    rows: Which rows to dispense into. Only sent by a step that stands on its own.
    in_wash: Whether the step belongs to a wash, which selects wells itself. Such a step stores
      and sends no selections, and its payload is seven bytes shorter.
    is_cycle_dispense: Whether a wash uses this step as its between-cycles dispense. Validation
      checks a step a wash does not run less strictly.
    is_bottom_wash: Whether a wash uses this step as its bottom wash.
    force_pre_dispense: Whether the owning wash pre-dispenses whatever the step itself says.
  """

  step_type: ClassVar[StepType] = StepType.STRIP_DISPENSE

  volume: int = 50
  flow_rate: int = 5
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=333))
  pre_dispense: PreDispense = field(
    default_factory=lambda: PreDispense(volume=50, flow_rate=5, count=2)
  )
  vacuum: VacuumDelay = field(default_factory=VacuumDelay)
  columns: WellMask = field(default_factory=WellMask.all_columns)
  rows: WellMask = field(default_factory=WellMask.all_rows)
  in_wash: bool = False
  is_cycle_dispense: bool = False
  is_bottom_wash: bool = False
  force_pre_dispense: bool = False

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    The four flags describing the step's place in a wash are not stored; the owning wash sets them
    again when it uses the step.

    Returns:
      The ``|``-separated definition, with the selections only when the step stands alone.
    """
    text = (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.volume}|{self.flow_rate}"
      f"|{self.positioning.to_definition()}|{self.pre_dispense.enabled}"
      f"|{self.pre_dispense.volume}|{self.pre_dispense.flow_rate}|{self.pre_dispense.count}"
      f"|{self.vacuum.to_definition()}"
    )
    if not self.in_wash:
      text += f"|{self.columns.to_definition()}|{self.rows.to_definition()}"
    return text

  @classmethod
  def from_definition(cls, text: str) -> StripDispense:
    """Read the step back from its definition text.

    Whether the selections are present is what says whether the step belongs to a wash. This
    layout keeps empty fields rather than dropping them.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout.
    """
    own = definition.own_fields_at_least(
      text, cls.step_type, _DEFINITION_FIELDS, empty_ok=True, defaults=cls.default_definition
    )
    (
      volume,
      flow_rate,
      z,
      x,
      y,
      pre_enabled,
      pre_volume,
      pre_flow_rate,
      pre_count,
      vacuum_enabled,
      vacuum_volume,
    ) = own[:11]
    masks = own[11:]
    return cls(
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
        flow_rate=definition.number(pre_flow_rate, 8),
        count=definition.number(pre_count, 8),
      ),
      vacuum=VacuumDelay.from_definition(vacuum_enabled, vacuum_volume),
      columns=WellMask.from_definition(masks[0]) if masks else WellMask.all_columns(),
      rows=WellMask.from_definition(masks[1]) if len(masks) > 1 else WellMask.all_rows(),
      in_wash=not masks,
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    A step inside a wash sends its pre-dispense flow rate where a standalone step sends its own,
    and pre-dispenses when either it or the wash asks for it.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    pre_dispensing = self.pre_dispense.enabled or self.force_pre_dispense
    payload = (
      u16(self.volume)
      + u8(self.flow_rate)
      + i16(self.positioning.x_steps)
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + u16(self.pre_dispense.volume if pre_dispensing else 0)
      + u8(self.pre_dispense.flow_rate if self.in_wash else self.flow_rate)
      + u8(self.pre_dispense.count)
      + u16(self.vacuum.wire_volume)
    )
    if self.in_wash:
      return pad(payload, _IN_WASH_PAYLOAD_LENGTH)
    return pad(payload + self.columns.to_bytes() + self.rows.to_bytes(), _PAYLOAD_LENGTH)
