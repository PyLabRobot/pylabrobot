"""Dispensing through a peristaltic pump."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_type import (
  CASSETTE_TYPE_TO_BYTE,
  CassetteType,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_flow_rate import (
  PERI_FLOW_RATE_TO_BYTE,
  PeriFlowRate,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PERI_PUMP_TO_BYTE, PeriPump
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import PreDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning

PAYLOAD_LENGTH = 23
DEFINITION_FIELDS = 13

NO_CASSETTE_REQUIREMENT = 255
NO_PUMP = 0

BYTE_TO_CASSETTE_TYPE: dict[int, CassetteType] = {
  value: key for key, value in CASSETTE_TYPE_TO_BYTE.items()
}
BYTE_TO_PERI_PUMP: dict[int, PeriPump] = {value: key for key, value in PERI_PUMP_TO_BYTE.items()}
PERI_FLOW_RATES: dict[str, PeriFlowRate] = {"Low": "Low", "Medium": "Medium", "High": "High"}


@dataclass
class PeriDispense(Step):
  """Dispense a volume into every selected well from a peristaltic pump.

  Dispensing into individually chosen wells instead is a different step, with a different payload
  and a different command: :class:`~.peri_random_access_dispense.PeriRandomAccessDispense`.

  Attributes:
    volume: Volume per tube in µL.
    flow_rate: How fast to dispense.
    cassette_type: The cassette the step requires, or None to accept whatever is fitted.
    positioning: Where in the well to dispense.
    pre_dispense: Whether to pre-dispense first, at what volume and how many times.
    columns: Which columns to dispense into.
    rows: Which rows to dispense into.
    peri_pump: Which pump to drive, or None to leave the choice to the instrument.
  """

  step_type: ClassVar[StepType] = StepType.PERI_DISPENSE

  volume: int = 10
  flow_rate: PeriFlowRate = "High"
  cassette_type: CassetteType | None = "Any"
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=333))
  pre_dispense: PreDispense = field(
    default_factory=lambda: PreDispense(enabled=True, volume=10, count=2)
  )
  columns: WellMask = field(default_factory=WellMask.all_columns)
  rows: WellMask = field(default_factory=WellMask.all_rows)
  peri_pump: PeriPump | None = "Primary"

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    The pre-dispense flow rate is not stored by this step type.

    Returns:
      The ``|``-separated definition.
    """
    cassette = (
      NO_CASSETTE_REQUIREMENT
      if self.cassette_type is None
      else CASSETTE_TYPE_TO_BYTE[self.cassette_type]
    )
    pump = NO_PUMP if self.peri_pump is None else PERI_PUMP_TO_BYTE[self.peri_pump]
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.volume}|{self.flow_rate}"
      f"|{cassette}|{self.positioning.to_definition()}|{self.pre_dispense.enabled}"
      f"|{self.pre_dispense.volume}|{self.pre_dispense.count}"
      f"|{self.columns.to_definition()}|{self.rows.to_definition()}|{pump}"
    )

  @classmethod
  def from_definition(cls, text: str) -> PeriDispense:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a flow rate,
        cassette or pump that does not exist. A definition carrying random-access fields is a
        different step type's and is rejected here.
    """
    (
      volume,
      flow_rate,
      cassette,
      z,
      x,
      y,
      pre_enabled,
      pre_volume,
      pre_count,
      columns,
      rows,
      pump,
    ) = definition.own_fields(
      text, cls.step_type, DEFINITION_FIELDS, defaults=cls.default_definition
    )
    if flow_rate not in PERI_FLOW_RATES:
      raise ValueError(f"unknown peristaltic flow rate: {flow_rate!r}")
    if int(cassette) != NO_CASSETTE_REQUIREMENT and int(cassette) not in BYTE_TO_CASSETTE_TYPE:
      raise ValueError(f"unknown cassette type: {cassette!r}")
    if int(pump) != NO_PUMP and int(pump) not in BYTE_TO_PERI_PUMP:
      raise ValueError(f"unknown peristaltic pump: {pump!r}")
    return cls(
      volume=definition.number(volume, 16),
      flow_rate=PERI_FLOW_RATES[flow_rate],
      cassette_type=BYTE_TO_CASSETTE_TYPE.get(int(cassette)),
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
      columns=WellMask.from_definition(columns),
      rows=WellMask.from_definition(rows),
      peri_pump=BYTE_TO_PERI_PUMP.get(int(pump)),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    With the wider dispense offsets fitted the payload drops the cassette requirement and spends
    the two bytes on a wider X offset instead. The row selection is sent inverted, so a selected
    row contributes a zero bit.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    pump = NO_PUMP if self.peri_pump is None else PERI_PUMP_TO_BYTE[self.peri_pump]
    if settings.advanced_dispense_offsets:
      offsets = i16(self.positioning.x_steps)
    else:
      cassette = (
        NO_CASSETTE_REQUIREMENT
        if self.cassette_type is None
        else CASSETTE_TYPE_TO_BYTE[self.cassette_type]
      )
      offsets = u8(cassette) + i8(self.positioning.x_steps)
    return pad(
      u16(self.volume)
      + u8(PERI_FLOW_RATE_TO_BYTE[self.flow_rate])
      + offsets
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + u16(self.pre_dispense.wire_volume)
      + u8(self.pre_dispense.count)
      + self.columns.to_bytes()
      + self.rows.to_bytes_inverted()
      + u8(pump),
      PAYLOAD_LENGTH,
    )
