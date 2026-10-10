"""Tests for aligning a fixed-grid head with a grid of the same pitch."""

import pytest

from pylabrobot.lib.liquid_handling.head_alignment import (
  get_items_under_channels,
  get_shift_with_channel_over,
)
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL_filter


def test_each_channel_stands_over_the_item_the_alignment_puts_under_it() -> None:
  # Channel H12 over spot D6: channels E7 to H12 stand over spots A1 to D6, the rest past the rack.
  rack = hamilton_96_tiprack_300uL_filter(name="tip_rack")
  under = {
    f"{'EFGH'[row]}{column + 7}": rack.get_item(f"{'ABCD'[row]}{column + 1}")
    for row in range(4)
    for column in range(6)
  }

  items = get_items_under_channels(rack, channel="H12", item="D6")

  channels = [rack.get_child_identifier(spot) for spot in rack.get_all_items()]
  assert items == [under.get(channel) for channel in channels]


def test_channel_a1_is_shifted_by_the_rows_and_columns_between_the_two() -> None:
  deck = Resource(name="deck", size_x=1000, size_y=600, size_z=0)
  rack = hamilton_96_tiprack_300uL_filter(name="tip_rack")
  deck.assign_child_resource(rack, location=Coordinate(100, 100, 0))

  shift = get_shift_with_channel_over(rack, channel="H12", item="D6", deck=deck)

  # 6 columns to the left and 4 rows behind, at the rack's 9 mm pitch.
  assert (shift.x, shift.y, shift.z) == pytest.approx((-54.0, 36.0, 0.0))
