"""Dispensing into individually chosen wells through a peristaltic pump."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_head import (
  CASSETTE_HEAD_TO_BYTE,
  CassetteHead,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_flow_rate import PERI_FLOW_RATE_TO_BYTE
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PERI_PUMP_TO_BYTE
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import (
  PreDispense,
  WellVolumeMap,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import (
  BYTE_TO_CASSETTE_TYPE,
  BYTE_TO_PERI_PUMP,
  DEFINITION_FIELDS,
  NO_CASSETTE_REQUIREMENT,
  NO_PUMP,
  PERI_FLOW_RATES,
  PeriDispense,
)

_PAYLOAD_LENGTH = 66
_TAIL_FIELDS = 3
_NO_CASSETTE_HEAD = 255

_BYTE_TO_CASSETTE_HEAD: dict[int, CassetteHead] = {
  value: key for key, value in CASSETTE_HEAD_TO_BYTE.items()
}


@dataclass
class PeriRandomAccessDispense(PeriDispense):
  """Dispense a volume of its own into each of sixteen chosen wells from a peristaltic pump.

  Stored as an ordinary peristaltic dispense with the head and the per-well volumes appended, so
  the column and row selections are stored too even though the instrument is sent the per-well
  volumes instead. It runs as a different command.

  A model old enough not to read the extra fields cannot run this step; validation rejects it
  rather than quietly running it as an ordinary dispense.

  Attributes:
    cassette_head: How the fitted cassette's tubes map onto wells, or None when the step names no
      head.
    well_volumes: The volume for each well, three tubes deep for each of sixteen wells.
  """

  step_type: ClassVar[StepType] = StepType.PERI_DISPENSE

  cassette_head: CassetteHead | None = None
  well_volumes: WellVolumeMap = field(default_factory=WellVolumeMap)

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition: an ordinary dispense followed by the head and the per-well
      volumes.
    """
    head = (
      _NO_CASSETTE_HEAD if self.cassette_head is None else CASSETTE_HEAD_TO_BYTE[self.cassette_head]
    )
    return f"{super().to_definition()}|True|{head}|{self.well_volumes.to_definition()}"

  @classmethod
  def from_definition(cls, text: str) -> PeriRandomAccessDispense:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a flow rate,
        cassette or pump that does not exist.
    """
    own = definition.own_fields(
      text, cls.step_type, DEFINITION_FIELDS + _TAIL_FIELDS, defaults=cls.default_definition
    )
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
      _random_access,
      head,
      well_volumes,
    ) = own
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
      cassette_head=_BYTE_TO_CASSETTE_HEAD.get(int(head)),
      well_volumes=WellVolumeMap.from_definition(well_volumes),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    The payload carries the per-well volumes where an ordinary dispense carries its selections,
    and has one layout: the wider dispense offsets make no difference to it, since the X offset is
    two bytes wide here either way.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    pump = NO_PUMP if self.peri_pump is None else PERI_PUMP_TO_BYTE[self.peri_pump]
    return pad(
      u16(self.volume)
      + u8(PERI_FLOW_RATE_TO_BYTE[self.flow_rate])
      + i16(self.positioning.x_steps)
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + u16(self.pre_dispense.wire_volume)
      + u8(self.pre_dispense.count)
      + bytes(value for row in self.well_volumes.values for value in row)
      + u8(pump),
      _PAYLOAD_LENGTH,
    )
