"""Dispensing through the wash manifold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.buffer import Buffer
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.packing import i8, i16, pad, u8, u16
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import (
  PreDispense,
  VacuumDelay,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning

_PAYLOAD_LENGTH = 19
_DEFINITION_FIELDS = 12


@dataclass
class ManifoldDispense(Step):
  """Dispense a volume into every selected well through the wash manifold.

  Attributes:
    buffer: Which buffer inlet to draw from.
    volume: Volume per well in µL.
    flow_rate: How fast to dispense. The two slowest rates need the cell washing module and a
      96-tube dual-action manifold.
    positioning: Where in the well to dispense.
    pre_dispense: Whether to pre-dispense first, and at what volume and rate.
    vacuum: Whether to hold the vacuum off until a volume has been dispensed.
    check_buffer: Whether validation checks the buffer. A wash clears this on the dispense it
      owns, which draws from the wash's own inlet.
    check_volume: Whether validation checks the volume and everything measured with it. A wash
      clears this on a bottom-wash dispense, whose volume only matters when that stage runs.
  """

  step_type: ClassVar[StepType] = StepType.MANIFOLD_DISPENSE

  buffer: Buffer = "A"
  volume: int = 0
  flow_rate: int = 7
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=120))
  pre_dispense: PreDispense = field(default_factory=lambda: PreDispense(volume=0, flow_rate=9))
  vacuum: VacuumDelay = field(default_factory=VacuumDelay)
  check_buffer: bool = True
  check_volume: bool = True

  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    The pre-dispense count is not stored by this step type, and neither validation flag is stored
    at all: a step read back from a protocol file checks both.

    Returns:
      The ``|``-separated definition.
    """
    return (
      f"{definition.FORMAT_MARKER}|{self.step_type.value}|{self.buffer}|{self.volume}"
      f"|{self.flow_rate}|{self.positioning.to_definition()}|{self.pre_dispense.enabled}"
      f"|{self.pre_dispense.volume}|{self.pre_dispense.flow_rate}"
      f"|{self.vacuum.to_definition()}"
    )

  @classmethod
  def from_definition(cls, text: str) -> ManifoldDispense:
    """Read the step back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this step type's layout, or names a buffer that
        does not exist.
    """
    (
      buffer,
      volume,
      flow_rate,
      z,
      x,
      y,
      pre_enabled,
      pre_volume,
      pre_flow_rate,
      vacuum_enabled,
      vacuum_volume,
    ) = definition.own_fields(
      text, cls.step_type, _DEFINITION_FIELDS, defaults=cls.default_definition
    )
    if buffer not in definition.BUFFERS:
      raise ValueError(f"unknown buffer: {buffer!r}")
    return cls(
      buffer=definition.BUFFERS[buffer],
      volume=definition.number(volume, 16),
      flow_rate=definition.number(flow_rate, 8),
      positioning=Positioning(
        z_steps=definition.signed(z, 16),
        x_steps=definition.signed(x, 8),
        y_steps=definition.signed(y, 8),
      ),
      pre_dispense=PreDispense(
        enabled=definition.flag(pre_enabled),
        volume=definition.number(pre_volume, 16),
        flow_rate=definition.number(pre_flow_rate, 8),
      ),
      vacuum=VacuumDelay.from_definition(vacuum_enabled, vacuum_volume),
    )

  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    The offsets are sent X, Y, Z, the reverse of the order they are stored in, with X and Y one
    byte each and Z two.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload.
    """
    return pad(
      u8(ord(self.buffer))
      + u16(self.volume)
      + u8(self.flow_rate)
      + i8(self.positioning.x_steps)
      + i8(self.positioning.y_steps)
      + i16(self.positioning.z_steps)
      + u16(self.pre_dispense.wire_volume)
      + u8(self.pre_dispense.flow_rate)
      + u16(self.vacuum.wire_volume),
      _PAYLOAD_LENGTH,
    )
