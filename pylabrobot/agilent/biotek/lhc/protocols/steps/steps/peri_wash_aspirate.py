"""Aspirating through the peristaltic wash manifold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PERI_PUMP_TO_BYTE, PeriPump
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning

_PAYLOAD_LENGTH = 22
_DEFINITION_FIELDS = 9

_NO_PUMP = 0
_BYTE_TO_PERI_PUMP: dict[int, PeriPump] = {value: key for key, value in PERI_PUMP_TO_BYTE.items()}


@dataclass
class PeriWashAspirate(Step):
  """Draw spent medium off gently through a peristaltic wash manifold.

  Paired with a peristaltic wash dispense, this exchanges medium without disturbing what is
  growing in the well, which an ordinary aspirate would draw out. It needs two peristaltic pumps
  with wash cassettes and manifolds fitted.

  Attributes:
    volume: Volume per tube in µL.
    flow_rate: How fast to aspirate, as a position on the aspirate rate scale.
    positioning: Where in the well to aspirate.
    peri_pump: Which pump to drive.
    columns: Which columns to aspirate.
    rows: Which row sections to aspirate.
  """

  step_type: ClassVar[StepType] = StepType.PERI_WASH_ASPIRATE

  volume: int = 100
  flow_rate: int = 2
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=22))
  peri_pump: PeriPump | None = "Primary"
  columns: WellMask = field(default_factory=WellMask.all_columns)
  rows: WellMask = field(default_factory=WellMask.all_rows)

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition.
    """
    pump = _NO_PUMP if self.peri_pump is None else PERI_PUMP_TO_BYTE[self.peri_pump]
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.volume}|{self.flow_rate}"
      f"|{self.positioning.to_definition()}|{pump}"
      f"|{self.columns.to_definition()}|{self.rows.to_definition()}"
    )

  @classmethod
  def from_definition(cls, text: str) -> PeriWashAspirate:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a pump that
        does not exist.
    """
    volume, flow_rate, z, x, y, pump, columns, rows = definition.own_fields(
      text,
      cls.step_type,
      _DEFINITION_FIELDS,
      defaults=cls.default_definition,
    )
    if int(pump) != _NO_PUMP and int(pump) not in _BYTE_TO_PERI_PUMP:
      raise ValueError(f"unknown peristaltic pump: {pump!r}")
    return cls(
      volume=definition.number(volume, 16),
      flow_rate=definition.number(flow_rate, 8),
      positioning=Positioning(
        z_steps=definition.signed(z, 16),
        x_steps=definition.signed(x, 16),
        y_steps=definition.signed(y, 8),
      ),
      peri_pump=_BYTE_TO_PERI_PUMP.get(int(pump)),
      columns=WellMask.from_definition(columns),
      rows=WellMask.from_definition(rows),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    The offsets are sent X, Y, Z, the reverse of the order they are stored in, and the row
    selection is sent inverted.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    pump = _NO_PUMP if self.peri_pump is None else PERI_PUMP_TO_BYTE[self.peri_pump]
    return pad(
      u16(self.volume)
      + u8(self.flow_rate)
      + i16(self.positioning.x_steps)
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + self.columns.to_bytes()
      + self.rows.to_bytes_inverted()
      + u8(pump),
      _PAYLOAD_LENGTH,
    )
