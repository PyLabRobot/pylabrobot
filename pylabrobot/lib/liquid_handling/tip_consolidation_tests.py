"""Tests for tip consolidation planning without hardware or tracker mutation."""

import random
from typing import List, Optional

import pytest

from pylabrobot.lib.liquid_handling.tip_consolidation import plan_tip_consolidation
from pylabrobot.resources import TipRack
from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL_filter


def _rack(name: str, occupied: List[int]) -> TipRack:
  """Create a rack with tips at the given integer indices."""
  rack = hamilton_96_tiprack_300uL_filter(name)
  rack.set_tip_state([index in occupied for index in range(96)])
  return rack


@pytest.mark.parametrize("channels", [[0], [1, 3, 5], list(range(8)), list(range(16))])
def test_consolidates_without_mutating_inventory(channels: List[int]) -> None:
  """Plans preserve tips and fill the fullest racks first for different channel counts."""
  rng = random.Random(42)
  racks = [_rack(str(i), rng.sample(range(96), count)) for i, count in enumerate([23, 71, 45])]
  spots = [spot for rack in racks for spot in rack.get_all_items()]
  before = {spot.name: spot.tracker.has_tip for spot in spots}
  batches = plan_tip_consolidation(racks, 16, lambda channel, tip: True, channels)
  assert {spot.name: spot.tracker.has_tip for spot in spots} == before

  after = dict(before)
  for batch in batches:
    count = len(batch.origin_tip_spots)
    assert 0 < count <= min(8, len(channels))
    assert len(batch.target_tip_spots) == count
    assert batch.use_channels == channels[:count]
    for origin, target in zip(batch.origin_tip_spots, batch.target_tip_spots):
      assert after[origin.name]
      assert not after[target.name]
      after[origin.name] = False
      after[target.name] = True

  assert sum(after.values()) == sum(before.values())
  assert [after[spot.name] for spot in racks[1].get_all_items()] == [True] * 96
  assert [after[spot.name] for spot in racks[2].get_all_items()] == [True] * 43 + [False] * 53
  assert not any(after[spot.name] for spot in racks[0].get_all_items())


def test_skips_full_empty_and_already_consolidated_racks() -> None:
  """Inventory that needs no transfers produces an empty plan."""
  racks = [_rack("full", list(range(96))), _rack("empty", []), _rack("partial", [0, 1])]
  assert plan_tip_consolidation(racks, 8, lambda channel, tip: True) == []
  assert plan_tip_consolidation([], 8, lambda channel, tip: True) == []


def test_selects_actual_compatible_channels_for_each_model() -> None:
  """Nonconsecutive channel IDs and model-specific capabilities survive planning."""
  first = _rack("first", [90, 91])
  second = _rack("second", [90, 91])
  for spot in second.get_all_items()[90:92]:
    spot.tracker.get_tip().model = "second_model"
  batches = plan_tip_consolidation(
    [first, second],
    8,
    lambda channel, tip: channel in ([1, 3] if tip.model == "second_model" else [4, 6]),
  )
  assert [batch.use_channels for batch in batches] == [[4, 6], [1, 3]]
  for batch in batches:
    assert batch.origin_tip_spots[0].parent is batch.target_tip_spots[0].parent


def test_keeps_destination_columns_together() -> None:
  """An eight-channel plan avoids splitting columns that fit in one batch."""
  rack = _rack("rack", [0, 1] + list(range(16, 30)))
  batches = plan_tip_consolidation([rack], 8, lambda channel, tip: True)
  assert [batch.target_tip_spots for batch in batches] == [rack["C1:H1"], rack["A2:H2"]]


@pytest.mark.parametrize("model", [None, "different"])
def test_rejects_undefined_or_mixed_models(model: Optional[str]) -> None:
  """A partial rack must contain one defined tip model."""
  rack = _rack("rack", [90, 91])
  rack.get_item(91).tracker.get_tip().model = model
  with pytest.raises(ValueError, match="Tip models must be defined|mixed tip models"):
    plan_tip_consolidation([rack], 8, lambda channel, tip: True)


@pytest.mark.parametrize("channels", [[], [1, 1], [-1], [8]])
def test_rejects_invalid_channel_selections(channels: List[int]) -> None:
  """Explicit channels must be nonempty, distinct, and within the device's range."""
  with pytest.raises(ValueError, match="use_channels"):
    plan_tip_consolidation([_rack("rack", [90])], 8, lambda channel, tip: True, channels)


def test_rejects_unavailable_channels() -> None:
  """No compatible channel yields a planning error before execution."""
  with pytest.raises(ValueError, match="No channel capable"):
    plan_tip_consolidation([_rack("rack", [90])], 8, lambda channel, tip: False)
