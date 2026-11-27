"""Groups of parameters that recur across step types.

Each class here covers one group of fields several step types carry, so a decision like the order
offsets are stored in is made once. A group that every step writes the same way knows its own
definition text; the two that different steps write different subsets of leave that to the step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_head import CassetteHead
from pylabrobot.agilent.biotek.lhc.enums.steps.secondary_aspirate_pattern import (
  SecondaryAspiratePattern,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_axis import ShakeAxis
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_intensity import ShakeIntensity
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.durations import (
  format_hours_minutes,
  format_minutes_seconds,
  parse_hours_minutes,
  parse_minutes_seconds,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning

_SHAKE_AXIS_TO_FIELD: dict[ShakeAxis, str] = {"X": "X-axis", "Y": "Y-axis"}
"""How a protocol file spells each shake axis."""

_SHAKE_INTENSITY_TO_FIELD: dict[ShakeIntensity, str] = {
  "Variable": "Variable",
  "Slow": "Slow (3.5 Hz)",
  "Medium": "Medium (5 Hz)",
  "Fast": "Fast (8 Hz)",
}
"""How a protocol file spells each shake intensity."""

_FIELD_TO_SHAKE_INTENSITY: dict[str, ShakeIntensity] = {
  "variable": "Variable",
  "slow": "Slow",
  "medium": "Medium",
  "fast": "Fast",
}
"""The first word of an intensity field, which is all that identifies it."""

_CASSETTE_HEAD_TO_FIELD: dict[CassetteHead, str] = {
  "8 tubes to 8 wells": "0",
  "8 tubes to 1 well": "1",
  "8 tubes to 1 chute": "2",
  "1 tube to 1 well": "3",
}
"""How a protocol file spells each cassette head. No head is stored as 255."""

_FIELD_TO_CASSETTE_HEAD: dict[str, CassetteHead] = {
  value: key for key, value in _CASSETTE_HEAD_TO_FIELD.items()
}

_SECONDARY_ASPIRATE_PATTERNS: dict[str, SecondaryAspiratePattern] = {
  "None": "None",
  "Point": "Point",
  "Circle": "Circle",
  "Square": "Square",
}


@dataclass
class PreDispense:
  """A small volume dispensed to waste before the step proper, to fill the lines.

  Which of these a step encodes differs per step type, so the step writes the fields it uses and
  ignores the rest.

  Attributes:
    enabled: Whether to pre-dispense at all.
    volume: Volume per tube in µL.
    flow_rate: Flow rate to pre-dispense at.
    count: How many pre-dispenses to perform.
  """

  enabled: bool = False
  volume: int = 50
  flow_rate: int = 9
  count: int = 2

  @property
  def wire_volume(self) -> int:
    """The volume as encoded, which is zero when pre-dispensing is off."""
    return self.volume if self.enabled else 0


@dataclass
class SecondaryAspirate:
  """A second aspirate at its own position, to clear what the first one leaves behind.

  Attributes:
    pattern: The path the tip traces, or ``"None"`` to skip the secondary aspirate.
    positioning: Where the secondary aspirate works.
  """

  pattern: SecondaryAspiratePattern = "None"
  positioning: Positioning = field(default_factory=lambda: Positioning(z_steps=30))

  @property
  def enabled(self) -> bool:
    """Whether a secondary aspirate runs."""
    return self.pattern != "None"

  def to_definition(self) -> str:
    """The four fields a protocol file stores.

    Returns:
      The fields, ``|``-separated.
    """
    return f"{self.pattern}|{self.positioning.to_definition()}"

  @classmethod
  def from_definition(cls, pattern: str, z: str, x: str, y: str) -> SecondaryAspirate:
    """Read the four fields back.

    Args:
      pattern: The pattern field.
      z: The depth field.
      x: The across-plate field.
      y: The along-plate field.

    Returns:
      The secondary aspirate.

    Raises:
      ValueError: If the pattern is not one of the four.
    """
    if pattern not in _SECONDARY_ASPIRATE_PATTERNS:
      raise ValueError(f"unknown secondary aspirate pattern: {pattern!r}")
    return cls(
      pattern=_SECONDARY_ASPIRATE_PATTERNS[pattern],
      positioning=Positioning.from_definition(z, x, y),
    )


@dataclass
class VacuumDelay:
  """Holding the vacuum off until a dispense has put a given volume in the well.

  Attributes:
    enabled: Whether to delay the vacuum.
    volume: Volume per well in µL to dispense first.
  """

  enabled: bool = False
  volume: int = 0

  @property
  def wire_volume(self) -> int:
    """The volume as encoded, which is zero when the delay is off."""
    return self.volume if self.enabled else 0

  def to_definition(self) -> str:
    """The two fields a protocol file stores.

    Returns:
      The fields, ``|``-separated.
    """
    return f"{self.enabled}|{self.volume}"

  @classmethod
  def from_definition(cls, enabled: str, volume: str) -> VacuumDelay:
    """Read the two fields back.

    Args:
      enabled: The flag field.
      volume: The volume field.

    Returns:
      The vacuum delay.
    """
    return cls(enabled=definition.flag(enabled), volume=definition.number(volume, 16))


@dataclass
class Submerge:
  """Leaving the tips standing in fluid once a prime has finished.

  Attributes:
    enabled: Whether to submerge.
    duration: How long to stay submerged, in seconds. The instrument runs this at whole-minute
      resolution.
  """

  enabled: bool = False
  duration: int = 300

  @property
  def wire_minutes(self) -> int:
    """The duration as encoded, in minutes, and zero when submerging is off."""
    return self.duration // 60 if self.enabled else 0

  def to_definition(self) -> str:
    """The two fields a protocol file stores.

    Returns:
      The fields, ``|``-separated.
    """
    return f"{self.enabled}|{format_hours_minutes(self.duration)}"

  @classmethod
  def from_definition(cls, enabled: str, duration: str) -> Submerge:
    """Read the two fields back.

    Args:
      enabled: The flag field.
      duration: The duration field.

    Returns:
      The submerge settings.
    """
    return cls(enabled=definition.flag(enabled), duration=parse_hours_minutes(duration))


@dataclass
class Shake:
  """Shaking the carrier.

  Attributes:
    enabled: Whether to shake.
    duration: How long to shake, in seconds.
    axis: Which axis to shake along.
    intensity: How vigorously to shake.
  """

  enabled: bool = False
  duration: int = 5
  axis: ShakeAxis = "X"
  intensity: ShakeIntensity = "Medium"

  @property
  def wire_duration(self) -> int:
    """The duration as encoded, in seconds, and zero when shaking is off."""
    return self.duration if self.enabled else 0

  def to_definition(self) -> str:
    """The four fields a protocol file stores.

    Returns:
      The fields, ``|``-separated.
    """
    return (
      f"{self.enabled}|{format_minutes_seconds(self.duration)}"
      f"|{_SHAKE_AXIS_TO_FIELD[self.axis]}|{_SHAKE_INTENSITY_TO_FIELD[self.intensity]}"
    )

  @classmethod
  def from_definition(cls, enabled: str, duration: str, axis: str, intensity: str) -> Shake:
    """Read the four fields back.

    An intensity is identified by its first word, and an unrecognised one reads as ``"Medium"``,
    which is the intensity the instrument shakes at when given one.

    Args:
      enabled: The flag field.
      duration: The duration field.
      axis: The axis field.
      intensity: The intensity field.

    Returns:
      The shake settings.
    """
    return cls(
      enabled=definition.flag(enabled),
      duration=parse_minutes_seconds(duration),
      axis="Y" if axis.strip().upper().startswith("Y") else "X",
      intensity=_FIELD_TO_SHAKE_INTENSITY.get(intensity.strip().split(" ")[0].lower(), "Medium"),
    )


@dataclass
class Soak:
  """Standing still for a while.

  Attributes:
    enabled: Whether to soak.
    duration: How long to soak, in seconds.
  """

  enabled: bool = False
  duration: int = 30

  @property
  def wire_duration(self) -> int:
    """The duration as encoded, in seconds, and zero when soaking is off."""
    return self.duration if self.enabled else 0

  def to_definition(self) -> str:
    """The two fields a protocol file stores.

    Returns:
      The fields, ``|``-separated.
    """
    return f"{self.enabled}|{format_minutes_seconds(self.duration)}"

  @classmethod
  def from_definition(cls, enabled: str, duration: str) -> Soak:
    """Read the two fields back.

    Args:
      enabled: The flag field.
      duration: The duration field.

    Returns:
      The soak settings.
    """
    return cls(enabled=definition.flag(enabled), duration=parse_minutes_seconds(duration))


@dataclass
class RandomAccess:
  """Dispensing into individually chosen wells rather than the whole plate.

  Attributes:
    enabled: Whether the step dispenses at random access.
    cassette_head: Which cassette head is fitted, or None when the step names no head.
  """

  enabled: bool = False
  cassette_head: CassetteHead | None = None

  def to_definition(self) -> str:
    """The two fields a protocol file stores, present only on a random-access step.

    Returns:
      The fields, ``|``-separated.
    """
    head = "255" if self.cassette_head is None else _CASSETTE_HEAD_TO_FIELD[self.cassette_head]
    return f"{self.enabled}|{head}"

  @classmethod
  def from_definition(cls, enabled: str, cassette_head: str) -> RandomAccess:
    """Read the two fields back.

    Args:
      enabled: The flag field.
      cassette_head: The head field.

    Returns:
      The random-access settings.
    """
    return cls(
      enabled=definition.flag(enabled),
      cassette_head=_FIELD_TO_CASSETTE_HEAD.get(cassette_head.strip()),
    )


@dataclass
class WellVolumeMap:
  """Per-well volumes for a random-access dispense, three tubes deep for each of sixteen wells.

  Attributes:
    values: Sixteen rows of three values each.
  """

  values: list[list[int]] = field(default_factory=lambda: [[0xFF] * 3 for _ in range(16)])

  def to_definition(self) -> str:
    """The single field a protocol file stores, two upper-case hex digits per value.

    Returns:
      The field text.
    """
    return "".join(f"{value:02X}" for row in self.values for value in row)

  @classmethod
  def from_definition(cls, field_text: str) -> WellVolumeMap:
    """Read the field back.

    Args:
      field_text: Two hex digits per value.

    Returns:
      The map.
    """
    values = [int(field_text[at : at + 2], 16) for at in range(0, len(field_text), 2)]
    return cls([values[row * 3 : row * 3 + 3] for row in range(len(values) // 3)])


@dataclass
class Sectors:
  """Which quarters of the plate a wash covers.

  Attributes:
    selected: One flag per sector, in sector order.
  """

  selected: list[bool] = field(default_factory=lambda: [True] * 4)

  @property
  def value(self) -> int:
    """The selection as a bit per sector, the first sector being the least significant bit."""
    return sum(1 << index for index, on in enumerate(self.selected) if on)

  def to_definition(self) -> str:
    """The single field a protocol file stores.

    Returns:
      The field text.
    """
    return str(self.value)

  @classmethod
  def from_definition(cls, field_text: str) -> Sectors:
    """Read the field back, which holds a bit per sector rather than a count.

    Args:
      field_text: The field text.

    Returns:
      The sector selection.
    """
    mask = definition.number(field_text, 16)
    return cls([bool(mask & (1 << index)) for index in range(4)])


@dataclass
class WashStages:
  """Which optional stages of a wash run.

  Each flag says whether its stage runs. The stages themselves are always stored, whether they run
  or not.

  Attributes:
    pre_dispense_before: Pre-dispense before the wash starts.
    bottom_wash: Wash the bottom of the well. Not available on a 1536-well wash.
    pre_dispense_between: Pre-dispense between wash cycles.
    final_aspirate: Aspirate once more after the last cycle.
    shake_soak_after_dispense: Shake or soak after each dispense.
  """

  pre_dispense_before: bool = False
  bottom_wash: bool = False
  pre_dispense_between: bool = False
  final_aspirate: bool = True
  shake_soak_after_dispense: bool = False

  def to_definition(self) -> str:
    """The five fields a protocol file stores, for the wash types that store them together.

    Returns:
      The fields, ``|``-separated.
    """
    return (
      f"{self.pre_dispense_before}|{self.bottom_wash}|{self.pre_dispense_between}"
      f"|{self.final_aspirate}|{self.shake_soak_after_dispense}"
    )

  @classmethod
  def from_definition(
    cls,
    pre_dispense_before: str,
    bottom_wash: str,
    pre_dispense_between: str,
    final_aspirate: str,
    shake_soak_after_dispense: str,
  ) -> WashStages:
    """Read the five fields back.

    Args:
      pre_dispense_before: The flag field.
      bottom_wash: The flag field.
      pre_dispense_between: The flag field.
      final_aspirate: The flag field.
      shake_soak_after_dispense: The flag field.

    Returns:
      The stage selection.
    """
    return cls(
      pre_dispense_before=definition.flag(pre_dispense_before),
      bottom_wash=definition.flag(bottom_wash),
      pre_dispense_between=definition.flag(pre_dispense_between),
      final_aspirate=definition.flag(final_aspirate),
      shake_soak_after_dispense=definition.flag(shake_soak_after_dispense),
    )
