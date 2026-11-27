"""What each step type encodes itself as.

The cross-reference next door compares nine of these against the single-device driver they replace,
which is the stronger check where it reaches. This covers what it cannot: the step types that driver
never had, the lengths every payload is padded to, and the places where what the instrument has
fitted changes the layout rather than the values.
"""

from __future__ import annotations

import pytest

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import StripWasherManifold
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_axis import ShakeAxis
from pylabrobot.agilent.biotek.lhc.enums.steps.shake_intensity import ShakeIntensity
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe import Syringe
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe_bottle import SyringeBottle
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Shake, Soak
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps import STEP_CLASSES
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.shake_soak import ShakeSoak
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_aspirate import StripAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_dispense import StripDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_dispense import SyringeDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_prime import SyringePrime

SETTINGS = InstrumentSettings()
"""A plainly equipped instrument, which is what a step encodes against unless a test says otherwise."""

WIDER_OFFSETS = InstrumentSettings(advanced_dispense_offsets=True)
"""One whose dispensers take the wider offset range, which changes how an offset is packed."""

LENGTHS: dict[StepType, int] = {
  StepType.PERI_DISPENSE: 23,
  StepType.PERI_PRIME: 10,
  StepType.PERI_PURGE: 10,
  StepType.SYRINGE_DISPENSE: 25,
  StepType.SYRINGE_PRIME: 12,
  StepType.MANIFOLD_WASH: 101,
  StepType.MANIFOLD_ASPIRATE: 21,
  StepType.MANIFOLD_DISPENSE: 19,
  StepType.MANIFOLD_PRIME: 12,
  StepType.MANIFOLD_AUTO_CLEAN: 7,
  StepType.SHAKE_SOAK: 11,
  StepType.WASH_1536: 67,
  StepType.STRIP_WASH: 108,
  StepType.STRIP_ASPIRATE: 27,
  StepType.STRIP_DISPENSE: 27,
  StepType.STRIP_PRIME: 12,
  StepType.PERI_WASH_ASPIRATE: 22,
  StepType.PERI_WASH_DISPENSE: 26,
}
"""How many bytes each step type sends. A payload is padded to its length and never truncated."""


STEP_TYPES: list[StepType] = sorted(LENGTHS, key=lambda member: member.value)
"""The step types, in the order they are numbered."""


@pytest.mark.parametrize("step_type", STEP_TYPES)
def test_a_payload_is_the_length_its_step_type_sends(step_type: StepType):
  """A payload is the length its step type sends."""
  assert len(STEP_CLASSES[step_type]().to_bytes(SETTINGS)) == LENGTHS[step_type]


def test_every_step_type_encodes():
  """Nothing is left to a base class that refuses: all eighteen have an encoder."""
  assert set(STEP_CLASSES) == set(LENGTHS)
  for step_type, cls in STEP_CLASSES.items():
    assert isinstance(cls().to_bytes(SETTINGS), bytes), step_type.name


# --- what is fitted can change the layout, not just the values.


def test_the_wider_offset_range_repacks_a_peristaltic_dispense():
  """The offset across the well goes from one byte to two when the dispensers take the wider range,
  which moves every field after it. The payload is the same length: the byte the offset grows into
  was padding."""
  step = PeriDispense(positioning=Positioning(x_steps=-30))
  narrow = step.to_bytes(SETTINGS)
  wide = step.to_bytes(WIDER_OFFSETS)
  assert narrow != wide
  assert len(narrow) == len(wide) == LENGTHS[StepType.PERI_DISPENSE]


def test_the_wider_offset_range_lengthens_a_syringe_dispense():
  """Here there was no padding to grow into, so the payload itself is a byte longer."""
  step = SyringeDispense(positioning=Positioning(x_steps=-30))
  assert len(step.to_bytes(SETTINGS)) == 25
  assert len(step.to_bytes(WIDER_OFFSETS)) == 26


def test_a_strip_dispense_sends_its_vacuum_volume_whatever_manifold_is_fitted():
  """A strip dispense sends its vacuum volume whatever manifold is fitted."""
  fitted = InstrumentSettings(strip_washer_manifold=StripWasherManifold.PLATE_96_WELL)
  step = StripDispense(volume=50)
  assert len(step.to_bytes(SETTINGS)) == len(step.to_bytes(fitted))


# --- a step a wash owns sends less than the same step standing alone.


@pytest.mark.parametrize(
  "standalone, in_wash, shorter_by",
  [
    (StripDispense(), StripDispense(in_wash=True), 7),
    (StripAspirate(), StripAspirate(in_wash=True), 7),
  ],
)
def test_a_strip_step_a_wash_owns_sends_seven_bytes_fewer(
  standalone: StripDispense | StripAspirate,
  in_wash: StripDispense | StripAspirate,
  shorter_by: int,
):
  """A wash selects the wells itself, so the steps it owns send no selections of their own."""
  assert len(standalone.to_bytes(SETTINGS)) - len(in_wash.to_bytes(SETTINGS)) == shorter_by


def test_a_wash_aspirate_sends_no_column_selection():
  """The plate washer's aspirate keeps its length either way and zeroes the selection instead."""
  columns = WellMask([1, 1] + [0] * 46)
  standalone = ManifoldAspirate(columns=columns).to_bytes(SETTINGS)
  in_wash = ManifoldAspirate(columns=columns, in_wash=True).to_bytes(SETTINGS)
  assert len(standalone) == len(in_wash)
  assert standalone != in_wash
  # Only the selection differs, and only by being dropped.
  differing = [i for i, (a, b) in enumerate(zip(standalone, in_wash)) if a != b]
  assert all(in_wash[i] == 0 for i in differing)


def test_a_column_selection_reaches_the_wire_as_twelve_bits():
  """Forty-eight entries go out as twelve: the instrument reads two per block of eight, and the
  rest are copies it never sees."""
  every_other = WellMask([1, 0] * 24)
  first_two = WellMask([1, 1] + [0] * 46)
  assert every_other.to_bits() != first_two.to_bits()
  assert first_two.to_bits() == 0b11
  assert WellMask.all_columns().to_bits() == 0xFFF


# --- the fields that are counted differently on the wire than in the API.


@pytest.mark.parametrize(
  "syringe, bottle, first_byte, bottle_byte",
  [
    ("A", "A1", 0, 0),
    ("B", "B2", 1, 3),
    ("Both", "A1B1", 2, 4),
  ],
)
def test_a_syringe_and_its_bottle_go_out_one_less_than_they_are_named(
  syringe: Syringe, bottle: SyringeBottle, first_byte: int, bottle_byte: int
):
  """Both are numbered from one in the vocabulary and from zero on the wire."""
  payload = SyringePrime(syringe=syringe, syringe_bottle=bottle).to_bytes(SETTINGS)
  assert payload[0] == first_byte
  assert payload[10] == bottle_byte


def test_shake_and_soak_durations_are_seconds():
  """Shake and soak durations are seconds."""
  payload = ShakeSoak(
    shake=Shake(enabled=True, duration=5), soak=Soak(enabled=True, duration=30)
  ).to_bytes(SETTINGS)
  assert int.from_bytes(payload[1:3], "little") == 5
  assert int.from_bytes(payload[5:7], "little") == 30


@pytest.mark.parametrize(
  "intensity, axis, intensity_byte, axis_byte",
  [
    ("Variable", "X", 1, 0),
    ("Slow", "X", 2, 0),
    ("Medium", "X", 3, 0),
    ("Fast", "Y", 4, 1),
  ],
)
def test_a_shake_names_its_intensity_and_axis_by_number(
  intensity: ShakeIntensity, axis: ShakeAxis, intensity_byte: int, axis_byte: int
):
  """A shake names its intensity and axis by number."""
  payload = ShakeSoak(
    shake=Shake(enabled=True, duration=5, axis=axis, intensity=intensity)
  ).to_bytes(SETTINGS)
  assert payload[3] == intensity_byte
  assert payload[4] == axis_byte
