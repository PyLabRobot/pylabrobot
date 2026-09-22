"""Building the column and row selections a step carries.

A selection is one entry per position rather than a list of numbers, so the constructors that turn
numbers into entries are what a caller reaches for. What is tested is that they place the entries
where the packers below them expect, and that a column outside the range is an error rather than a
silently dropped entry.
"""

from __future__ import annotations

import pytest

from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import COLUMNS, WellMask


def test_named_columns_are_the_ones_selected():
  mask = WellMask.from_columns([1, 2])

  assert mask.values == [1, 1] + [0] * 46
  assert mask.selected_count == 2


def test_a_selection_has_an_entry_per_position_whatever_is_named():
  assert len(WellMask.from_columns([1]).values) == COLUMNS
  assert len(WellMask.from_columns([]).values) == COLUMNS


def test_order_and_repeats_do_not_matter():
  assert WellMask.from_columns([3, 1, 3]) == WellMask.from_columns([1, 3])


def test_naming_no_column_selects_nothing():
  mask = WellMask.from_columns([])

  assert mask.selected_count == 0
  assert mask.is_valid


def test_a_column_beyond_the_plate_is_still_a_column():
  assert WellMask.from_columns([48]).values[-1] == 1


@pytest.mark.parametrize("columns", [[0], [49], [1, 0], [-1]])
def test_a_column_outside_the_range_is_refused(columns: list[int]):
  with pytest.raises(ValueError, match="columns must be 1..48"):
    WellMask.from_columns(columns)


def test_a_built_selection_packs_as_the_same_bytes_as_a_written_one():
  built = WellMask.from_columns([1, 2])
  written = WellMask([1, 1] + [0] * 46)

  assert built.to_bytes() == written.to_bytes()
  assert built.to_definition() == written.to_definition()


def test_every_column_is_what_all_columns_gives():
  assert WellMask.from_columns(range(1, COLUMNS + 1)) == WellMask.all_columns()
