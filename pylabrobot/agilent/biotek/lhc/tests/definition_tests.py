"""Reading a step back out of the text a protocol file stores it as.

Every definition here is one a protocol file really carries, or one this package writes itself. The
framing they all share is what is tested: locating the step-type field, the optional format marker,
the optional tails, and what a definition that is malformed rather than merely invalid does.
"""

from __future__ import annotations

import pytest

from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps import definition, step_from_definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import Submerge
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps import STEP_CLASSES
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_prime import ManifoldPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_wash import ManifoldWash
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_random_access_dispense import (
  PeriRandomAccessDispense,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_dispense import SyringeDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_prime import SyringePrime

ALL_COLUMNS = "1" * 48
"""A column selection with every column selected, as a definition spells it."""

WASH = (
  "6|Plate|15|3|False|False|False|True|False"
  "#8|A|250|7|120|0|0|False|50|9|False|10"
  "#8|False|6 CW|0|50|0|0|None|30|0|0|5.0"
  "#8|A|500|1|115|-45|0|False|50|9|True|50"
  "#11|True|False|00:05|X-axis|Medium (5 Hz)|False|00:30"
  "#8|False|6 CW|0|50|0|0|None|30|0|0|5.0"
)
"""A wash from a protocol file, whose two aspirate sub-steps both claim to be dispenses."""


def test_the_definition_says_which_step_it_is():
  """The definition says which step it is."""
  step = step_from_definition("DV103|9|A|40|9|True|5|True|04:00")
  assert isinstance(step, ManifoldPrime)
  assert step.step_type is StepType.MANIFOLD_PRIME


def test_every_field_lands_where_the_writer_puts_it():
  """Every field lands where the writer puts it."""
  step = ManifoldPrime.from_definition("DV103|9|C|95|9|True|50|True|00:20")
  assert step == ManifoldPrime(
    buffer="C",
    volume=95_000,
    flow_rate=9,
    prime_low_flow_path=True,
    low_flow_path_volume=50_000,
    submerge=Submerge(enabled=True, duration=1200),
  )


def test_volumes_are_microlitres_stored_at_millilitre_resolution():
  """A prime's volume is µL in the API and millilitres in the file, so only whole millilitres
  survive a round trip -- which is what the instrument runs anyway."""
  assert ManifoldPrime.from_definition("DV103|9|A|95|9|True|50|False|00:05").volume == 95_000
  assert ManifoldPrime(volume=95_400).to_definition().split("|")[3] == "95"


@pytest.mark.parametrize(
  "text",
  [
    "DV103|9|A|300|9|True|200|True|00:20",
    "DV101|9|A|300|9|True|200|True|00:20",
    "9|A|300|9|True|200|True|00:20",
  ],
)
def test_the_format_marker_is_optional_and_is_not_kept(text: str):
  """All three shapes are in the protocol files. Writing a step back always writes the current
  marker, so the version a definition arrived with is not carried."""
  step = ManifoldPrime.from_definition(text)
  assert step.volume == 300_000
  assert step.to_definition() == "DV103|9|A|300|9|True|200|True|00:20"


def test_a_newer_marker_is_refused_whole():
  """A newer marker is refused whole."""
  with pytest.raises(ValueError, match="newer than DV103"):
    ManifoldPrime.from_definition("DV104|9|A|40|5|True|5|True|04:00")


def test_fields_reports_where_the_step_type_sits():
  """Fields reports where the step type sits."""
  assert definition.fields("DV103|9|A") == (["DV103", "9", "A"], 1)
  assert definition.fields("9|A") == (["9", "A"], 0)


@pytest.mark.parametrize(
  "found, older",
  [
    (["DV103", "9"], False),
    (["DV101", "9"], True),
    (["9"], True),
  ],
)
def test_only_a_definition_older_than_the_current_format_may_be_short(
  found: list[str], older: bool
):
  """Only a definition older than the current format may be short."""
  assert definition.is_older_format(found) is older


def test_a_sub_steps_own_type_field_is_not_read():
  """A composite's sub-steps are read by position, and have to be: both aspirates in this wash say
  they are dispenses, and the file is a valid one. Only the outermost type field chooses a class."""
  wash = step_from_definition(WASH)
  assert isinstance(wash, ManifoldWash)
  assert wash.aspirate.step_type is StepType.MANIFOLD_ASPIRATE
  assert wash.aspirate.travel_rate == "6 CW"
  assert wash.aspirate.in_wash


def test_every_step_type_reads_back_as_its_own_class():
  """Every step type reads back as its own class."""
  for step_type, cls in STEP_CLASSES.items():
    text = cls().to_definition()
    assert type(step_from_definition(text)) is cls, step_type.name


# --- the optional tails: for several step types the field count is itself information.


def test_a_random_access_tail_makes_it_a_different_step():
  """Three more fields -- the flag, the head and the per-well volumes -- are a dispense into
  individually chosen wells, which runs as a different command."""
  plain = step_from_definition(f"DV103|1|2|High|1|333|0|0|True|10|4|{ALL_COLUMNS}|1111|1")
  assert type(plain) is PeriDispense

  with_tail = step_from_definition(
    f"DV103|1|2|High|1|333|0|0|True|10|4|{ALL_COLUMNS}|1111|1|True|3|" + "02" * 48
  )
  assert type(with_tail) is PeriRandomAccessDispense
  assert with_tail.cassette_head == "1 tube to 1 well"
  assert with_tail.well_volumes.values[0] == [2, 2, 2]


def test_a_row_selection_is_what_the_field_count_means():
  """Only the instruments that select rows store one, so the count records whether the instrument
  that wrote the definition did."""
  without = SyringeDispense.from_definition(f"DV103|4|1|50|2|336|0|0|True|50|2|0|{ALL_COLUMNS}|1")
  assert not without.selects_rows

  with_rows = SyringeDispense.from_definition(
    f"DV103|4|1|50|2|336|0|0|True|50|2|0|{ALL_COLUMNS}|1|1111"
  )
  assert with_rows.selects_rows
  assert with_rows.rows.to_definition() == "1111"


def test_a_step_that_stands_alone_keeps_its_column_selection():
  """A wash selects wells itself, so the aspirate it owns stores none -- and the count is what
  carries that across a save and a load."""
  standalone = ManifoldAspirate.from_definition(
    f"DV103|7|False|4|0|32|-50|8|None|29|0|0|0|{ALL_COLUMNS}"
  )
  assert not standalone.in_wash
  assert standalone.columns.to_definition() == ALL_COLUMNS

  in_wash = ManifoldAspirate.from_definition("DV103|7|False|4|0|32|-50|8|None|29|0|0|0")
  assert in_wash.in_wash
  assert in_wash.columns.to_definition() == ALL_COLUMNS


# --- filling in what an older release did not write.


def _tail_is_all_that_was_added(text: str, written: str) -> bool:
  """Whether writing a step back reproduces every field the definition it came from carried.

  Args:
    text: The definition that was read.
    written: The definition the step wrote.

  Returns:
    Whether the fields present line up, which is what makes filling a tail safe.
  """
  original = [part for part in text.split("|") if part]
  start = 1 if original[0].startswith("DV") else 0
  return written.split("|")[1 : len(original) - start + 1] == original[start:]


def test_an_older_syringe_prime_is_filled_from_the_defaults():
  """An instrument with one syringe box writes no submerge pair and no bottle, so the three fields
  the current layout ends with are absent."""
  text = "5|1|8000|5|5|0|True"
  step = SyringePrime.from_definition(text)
  assert (step.volume, step.flow_rate, step.cycles) == (8000, 5, 5)
  assert step.syringe == "A"
  assert step.syringe_bottle == SyringePrime().syringe_bottle
  assert _tail_is_all_that_was_added(text, step.to_definition())


def test_an_older_peristaltic_dispense_is_filled_from_the_defaults():
  """An instrument with a single peristaltic pump writes no row selection and no pump selector."""
  text = f"DV101|1|6|Low|1|254|0|0|True|6|2|{ALL_COLUMNS}"
  step = PeriDispense.from_definition(text)
  assert step.volume == 6
  assert step.flow_rate == "Low"
  assert step.peri_pump == PeriDispense().peri_pump
  assert step.rows.to_definition() == PeriDispense().rows.to_definition()
  assert _tail_is_all_that_was_added(text, step.to_definition())


def test_a_short_definition_of_the_current_format_is_another_products():
  """Six fields under the current marker is a different model's layout, which is short in the
  middle rather than at the end, so filling a tail would read every later field as the wrong one."""
  with pytest.raises(ValueError, match="expects 8 fields, got 6"):
    ManifoldPrime.from_definition("DV103|9|A|60|5|True|00:05")


def test_a_definition_at_full_length_is_untouched():
  """A definition at full length is untouched."""
  full = "DV103|5|1|5000|5|5|0|True|False|00:05|1"
  assert SyringePrime.from_definition(full).to_definition() == full


# --- what makes a definition unreadable rather than merely invalid.


@pytest.mark.parametrize(
  "text, problem",
  [
    ("DV103|9|A|40|5|True|5|True|04:00|1", "expects 8 fields"),
    ("DV103|9|A|70000|5|True|5|True|04:00", "does not fit in 16"),
    ("DV103|9|A|40|500|True|5|True|04:00", "does not fit in 8"),
    ("DV103|9|A|40|5|1|5|True|04:00", "not a boolean"),
    ("DV103|9|E|40|5|True|5|True|04:00", "unknown buffer"),
  ],
)
def test_a_field_that_will_not_read_raises(text: str, problem: str):
  """Reading has to fail rather than leave the step holding a default: a step half read from a
  protocol would run something the protocol did not ask for."""
  with pytest.raises(ValueError, match=problem):
    ManifoldPrime.from_definition(text)


def test_an_empty_field_does_not_hold_its_place():
  """An empty field is a missing one for most step types, so it shifts every field after it and the
  count is what catches it."""
  with pytest.raises(ValueError, match="expects 8 fields, got 7"):
    ManifoldPrime.from_definition("DV103|9|A||5|True|5|True|04:00")
