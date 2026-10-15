"""Tests for probing tip presence by pick-up, against a fake device's calls."""

import asyncio
import math
import random
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pytest

from pylabrobot.lib.liquid_handling.tip_presence_probing import (
  TipProbeBatch,
  plan_tip_inventory,
  plan_tip_presence_probes,
  probe_tip_inventory,
  probe_tip_presence_via_pickup,
)
from pylabrobot.resources import Coordinate, Deck, TipRack, TipSpot
from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL_filter


def _rack(name: str = "rack") -> TipRack:
  rack = hamilton_96_tiprack_300uL_filter(name)
  rack.set_tip_state([True] * 96)
  return rack


def _spots(rack: TipRack, wells: str) -> List[TipSpot]:
  return [rack.get_item(well) for well in wells.split()]


def _names(spots: Sequence[TipSpot]) -> List[str]:
  return [spot.name for spot in spots]


class _Device:
  """Channels over `empty` spots come up without a tip, and the pick-up then raises, as a device's.

  Every call is recorded; the sensors report what each channel holds.
  """

  def __init__(
    self,
    empty: Optional[Set[str]] = None,
    num_channels: int = 8,
    sensors_fail: bool = False,
    drop_fails: bool = False,
  ):
    self.empty = empty or set()
    self.held: List[Optional[str]] = [None] * num_channels
    self.sensors_fail = sensors_fail
    self.drop_fails = drop_fails
    self.calls: List[Tuple[str, List[str], List[int]]] = []
    self.returned: Dict[str, int] = {}

  async def pick_up_tips(self, spots: List[TipSpot], channels: List[int]) -> None:
    self.calls.append(("pick up", _names(spots), list(channels)))
    for spot, channel in zip(spots, channels):
      assert self.held[channel] is None, f"channel {channel} already holds a tip"
      if spot.name not in self.empty:
        self.held[channel] = spot.name
    if any(spot.name in self.empty for spot in spots):
      raise RuntimeError("a tip is not held")

  async def drop_tips(self, spots: List[TipSpot], channels: List[int]) -> None:
    self.calls.append(("drop", _names(spots), list(channels)))
    if self.drop_fails:
      raise RuntimeError("the drop stalled")
    for spot, channel in zip(spots, channels):
      assert self.held[channel] == spot.name, f"channel {channel} puts back a tip it did not take"
      self.held[channel] = None
      self.returned[spot.name] = self.returned.get(spot.name, 0) + 1

  async def sense_tip_presence(self) -> Sequence[bool]:
    if self.sensors_fail:
      raise ConnectionError("no answer")
    return [held is not None for held in self.held]

  def kwargs(self):
    return {
      "pick_up_tips": self.pick_up_tips,
      "drop_tips": self.drop_tips,
      "sense_tip_presence": self.sense_tip_presence,
    }


def _probe(device: _Device, spots: List[TipSpot], channels: List[int]) -> Dict[str, bool]:
  return asyncio.run(probe_tip_presence_via_pickup(spots, channels, **device.kwargs()))


def _inventory(device: _Device, spots: List[TipSpot], channels: List[int]) -> Dict[str, bool]:
  return asyncio.run(probe_tip_inventory(spots, channels, **device.kwargs()))


# -- planning ----------------------------------------------------------------------------------


def test_the_plan_takes_one_x_at_a_time_in_ascending_x_each_spot_keeping_its_channel():
  rack = _rack()
  spots = _spots(rack, "A2 A1 B2 B1")
  batches = plan_tip_presence_probes(spots, [0, 1, 2, 3])
  assert [_names(b.tip_spots) for b in batches] == [
    ["rack_tipspot_A1", "rack_tipspot_B1"],
    ["rack_tipspot_A2", "rack_tipspot_B2"],
  ]
  assert [b.use_channels for b in batches] == [[1, 3], [0, 2]]


def test_the_plan_keeps_the_order_given_within_one_x():
  rack = _rack()
  spots = _spots(rack, "C1 A1 B1")
  (batch,) = plan_tip_presence_probes(spots, [5, 3, 4])
  assert batch == TipProbeBatch(tip_spots=spots, use_channels=[5, 3, 4])


def test_the_plan_parts_two_racks_at_different_deck_x_whose_columns_share_a_local_x():
  """Grouped by where the spot stands on the deck, not inside its rack."""
  deck = Deck(size_x=1000, size_y=600, size_z=200)
  left, right = _rack("left"), _rack("right")
  deck.assign_child_resource(left, location=Coordinate(100, 100, 0))
  deck.assign_child_resource(right, location=Coordinate(300, 100, 0))
  spots = [left.get_item("A1"), right.get_item("A1")]
  assert spots[0].location == spots[1].location
  batches = plan_tip_presence_probes(spots, [0, 1])
  assert [_names(b.tip_spots) for b in batches] == [["left_tipspot_A1"], ["right_tipspot_A1"]]


def test_the_plan_joins_two_racks_that_stand_at_the_same_deck_x():
  """One behind the other: the channels reach both in one pick-up."""
  deck = Deck(size_x=1000, size_y=600, size_z=200)
  back, front = _rack("back"), _rack("front")
  deck.assign_child_resource(back, location=Coordinate(100, 200, 0))
  deck.assign_child_resource(front, location=Coordinate(100, 50, 0))
  (batch,) = plan_tip_presence_probes([back.get_item("H1"), front.get_item("A1")], [0, 1])
  assert _names(batch.tip_spots) == ["back_tipspot_H1", "front_tipspot_A1"]


def test_the_plan_of_nothing_is_nothing():
  assert plan_tip_presence_probes([], []) == []


@pytest.mark.parametrize(
  ("channels", "match"),
  [([0, 0], "named once"), ([0], "one channel per tip spot"), ([0, 1, 2], "one channel per")],
)
def test_the_plan_refuses_a_channel_twice_or_a_count_that_does_not_match(channels, match):
  spots = _spots(_rack(), "A1 B1")
  with pytest.raises(ValueError, match=match):
    plan_tip_presence_probes(spots, channels)


def test_planning_changes_no_tracker():
  rack = _rack()
  rack.set_tip_state([index % 3 == 0 for index in range(96)])
  before = [spot.tracker.has_tip for spot in rack.get_all_items()]
  plan_tip_presence_probes(rack.get_all_items()[:8], list(range(8)))
  assert [spot.tracker.has_tip for spot in rack.get_all_items()] == before


# -- probing one set -----------------------------------------------------------------------------


def test_every_spot_full_picks_up_and_drops_back_each_column_once():
  spots = _spots(_rack(), "A1 B1 A2 B2")
  device = _Device()
  found = _probe(device, spots, [0, 1, 2, 3])
  assert all(found.values()) and list(found) == _names(spots)
  assert [kind for kind, _, _ in device.calls] == ["pick up", "drop", "pick up", "drop"]
  assert device.returned == {name: 1 for name in _names(spots)}
  assert device.held == [None] * 8


def test_an_empty_spot_is_found_by_the_sensors_and_the_other_tips_go_back_to_their_own_spots():
  spots = _spots(_rack(), "A1 B1 C1")
  device = _Device(empty={spots[1].name})
  found = _probe(device, spots, [0, 1, 2])
  assert found == {spots[0].name: True, spots[1].name: False, spots[2].name: True}
  assert device.calls[-1] == ("drop", [spots[0].name, spots[2].name], [0, 2])
  assert device.held == [None] * 8


def test_every_spot_empty_drops_nothing():
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty=set(_names(spots)))
  assert _probe(device, spots, [0, 1]) == {name: False for name in _names(spots)}
  assert [kind for kind, _, _ in device.calls] == ["pick up"]


def test_an_empty_spot_in_one_column_leaves_the_other_columns_probed():
  spots = _spots(_rack(), "A1 B1 A2 B2 A3")
  device = _Device(empty={spots[3].name})
  found = _probe(device, spots, [0, 1, 2, 3, 4])
  assert [found[s.name] for s in spots] == [True, True, True, False, True]
  assert [kind for kind, _, _ in device.calls] == ["pick up", "drop"] * 3


def test_the_channels_given_are_the_channels_used():
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty={spots[0].name})
  _probe(device, spots, [6, 2])
  assert device.calls == [
    ("pick up", _names(spots), [6, 2]),
    ("drop", [spots[1].name], [2]),
  ]


def test_integer_sensor_readings_count_as_flags():
  """A device whose sensors answer 0 / 1, as the STAR's `C0RT` does."""
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty={spots[1].name})

  async def sensed() -> Sequence[bool]:
    return [int(held is not None) for held in device.held]  # type: ignore[misc]

  found = asyncio.run(
    probe_tip_presence_via_pickup(
      spots,
      [0, 1],
      pick_up_tips=device.pick_up_tips,
      drop_tips=device.drop_tips,
      sense_tip_presence=sensed,
    )
  )
  assert found == {spots[0].name: True, spots[1].name: False}


def test_probing_nothing_sends_nothing():
  device = _Device()
  assert _probe(device, [], []) == {}
  assert device.calls == []


def test_the_probe_leaves_the_trackers_to_the_devices_calls():
  rack = _rack()
  spots = _spots(rack, "A1 B1")
  _probe(_Device(empty={spots[0].name}), spots, [0, 1])
  assert [spot.tracker.has_tip for spot in spots] == [True, True]


# -- failures that are not an empty spot -------------------------------------------------------


def test_a_failure_the_sensors_do_not_explain_is_raised():
  """Every channel holds a tip after the error: something other than an empty spot failed."""
  spots = _spots(_rack(), "A1 B1")
  device = _Device()

  async def stalled(tip_spots, channels):
    for spot, channel in zip(tip_spots, channels):
      device.held[channel] = spot.name
    raise RuntimeError("the arm stalled")

  with pytest.raises(RuntimeError, match="stalled"):
    asyncio.run(
      probe_tip_presence_via_pickup(
        spots,
        [0, 1],
        pick_up_tips=stalled,
        drop_tips=device.drop_tips,
        sense_tip_presence=device.sense_tip_presence,
      )
    )


def test_sensors_that_cannot_be_read_raise_the_pickups_error():
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty={spots[0].name}, sensors_fail=True)
  with pytest.raises(RuntimeError, match="not held"):
    _probe(device, spots, [0, 1])


def test_a_drop_that_fails_is_raised_not_passed_over():
  spots = _spots(_rack(), "A1 B1")
  device = _Device(drop_fails=True)
  with pytest.raises(RuntimeError, match="drop stalled"):
    _probe(device, spots, [0, 1])


def test_a_failure_in_a_later_column_leaves_the_earlier_ones_put_back():
  spots = _spots(_rack(), "A1 A2")
  device = _Device()
  calls = 0

  async def second_fails(tip_spots, channels):
    nonlocal calls
    calls += 1
    if calls == 2:
      raise RuntimeError("the arm stalled")
    await device.pick_up_tips(tip_spots, channels)

  async def sensed() -> Sequence[bool]:
    return [True] * 8

  with pytest.raises(RuntimeError, match="stalled"):
    asyncio.run(
      probe_tip_presence_via_pickup(
        spots,
        [0, 1],
        pick_up_tips=second_fails,
        drop_tips=device.drop_tips,
        sense_tip_presence=sensed,
      )
    )
  assert device.returned == {spots[0].name: 1}


def test_a_cancellation_is_not_caught():
  spots = _spots(_rack(), "A1")
  device = _Device()

  async def cancelled(tip_spots, channels):
    raise asyncio.CancelledError()

  with pytest.raises(asyncio.CancelledError):
    asyncio.run(
      probe_tip_presence_via_pickup(
        spots,
        [0],
        pick_up_tips=cancelled,
        drop_tips=device.drop_tips,
        sense_tip_presence=device.sense_tip_presence,
      )
    )


def test_a_refused_plan_sends_nothing():
  spots = _spots(_rack(), "A1 B1")
  device = _Device()
  with pytest.raises(ValueError, match="named once"):
    _probe(device, spots, [0, 0])
  assert device.calls == []


# -- the channels named by the error ------------------------------------------------------


class _NamedError(Exception):
  """A pick-up error that names the channels that failed, as a channelized error does."""

  def __init__(self, channels):
    super().__init__(f"channels {channels} failed")
    self.channels = channels


def _named(error: Exception):
  return error.channels if isinstance(error, _NamedError) else None


def _probe_named(device, spots, channels, pick_up, sensors=None):
  return asyncio.run(
    probe_tip_presence_via_pickup(
      spots,
      channels,
      pick_up_tips=pick_up,
      drop_tips=device.drop_tips,
      sense_tip_presence=sensors,
      missed_channels=_named,
    )
  )


def test_the_channels_the_error_names_are_the_ones_missed_without_any_sensor():
  spots = _spots(_rack(), "A1 B1 C1")
  device = _Device(empty={spots[1].name})

  async def named(tip_spots, channels):
    try:
      await device.pick_up_tips(tip_spots, channels)
    except RuntimeError:
      raise _NamedError([1])

  found = _probe_named(device, spots, [0, 1, 2], named)
  assert found == {spots[0].name: True, spots[1].name: False, spots[2].name: True}
  assert device.calls[-1] == ("drop", [spots[0].name, spots[2].name], [0, 2])


def test_the_error_is_read_before_the_sensors():
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty={spots[0].name})

  async def named(tip_spots, channels):
    try:
      await device.pick_up_tips(tip_spots, channels)
    except RuntimeError:
      raise _NamedError([0])

  async def sensors_that_must_not_be_read() -> Sequence[bool]:
    raise AssertionError("the sensors were read though the error named the channels")

  found = _probe_named(device, spots, [0, 1], named, sensors=sensors_that_must_not_be_read)
  assert found == {spots[0].name: False, spots[1].name: True}


def test_an_error_that_names_no_channel_falls_back_to_the_sensors():
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty={spots[1].name})
  found = _probe_named(
    device, spots, [0, 1], device.pick_up_tips, sensors=device.sense_tip_presence
  )
  assert found == {spots[0].name: True, spots[1].name: False}


def test_an_error_that_names_no_channel_and_no_sensors_is_raised():
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty={spots[1].name})
  with pytest.raises(RuntimeError, match="not held"):
    _probe_named(device, spots, [0, 1], device.pick_up_tips)


def test_an_error_naming_a_channel_outside_the_pickup_is_raised():
  spots = _spots(_rack(), "A1 B1")
  device = _Device()

  async def names_another(tip_spots, channels):
    raise _NamedError([4])

  with pytest.raises(_NamedError):
    _probe_named(device, spots, [0, 1], names_another)


def test_an_error_naming_no_failed_channel_is_raised():
  spots = _spots(_rack(), "A1 B1")
  device = _Device()

  async def names_none(tip_spots, channels):
    raise _NamedError([])

  with pytest.raises(_NamedError):
    _probe_named(device, spots, [0, 1], names_none)


def test_only_the_pickups_own_channels_are_read_on_the_sensors():
  """An idle channel senses no tip; it is not taken for a spot that gave none."""
  spots = _spots(_rack(), "A1 B1")
  device = _Device(empty={spots[1].name})
  found = _probe(device, spots, [2, 5])
  assert found == {spots[0].name: True, spots[1].name: False}


# -- the inventory ----------------------------------------------------------------------------


def test_the_inventory_deals_the_spots_to_the_channels_given_in_turn():
  spots = _spots(_rack(), "A1 B1 C1 D1 E1")
  device = _Device(empty={spots[3].name})
  found = _inventory(device, spots, [0, 1])
  assert [found[s.name] for s in spots] == [True, True, True, False, True]
  picks = [channels for kind, _, channels in device.calls if kind == "pick up"]
  assert picks == [[0, 1], [0, 1], [0]]


def test_the_inventory_uses_the_first_channels_when_there_are_fewer_spots():
  spots = _spots(_rack(), "A1 B1")
  device = _Device()
  _inventory(device, spots, [3, 4, 5, 6])
  assert device.calls[0] == ("pick up", _names(spots), [3, 4])


def test_the_inventory_of_a_whole_rack_finds_every_empty_spot():
  rack = _rack()
  empty = {spot.name for i, spot in enumerate(rack.get_all_items()) if i % 7 == 3}
  device = _Device(empty=empty)
  found = _inventory(device, rack.get_all_items(), list(range(8)))
  assert {name for name, present in found.items() if not present} == empty
  assert len(found) == 96
  assert device.held == [None] * 8
  assert sum(device.returned.values()) == 96 - len(empty)


def test_the_inventory_refuses_no_channels_and_a_channel_twice():
  spots = _spots(_rack(), "A1 B1")
  device = _Device()
  with pytest.raises(ValueError, match="must not be empty"):
    _inventory(device, spots, [])
  with pytest.raises(ValueError, match="named once"):
    _inventory(device, spots, [1, 1])
  assert device.calls == []


# -- scattered spots ---------------------------------------------------------------------------


@pytest.mark.parametrize("channels", [[0, 1], list(range(8))])
def test_scattered_spots_take_no_more_pick_ups_than_their_columns_need(channels):
  rack = _rack()
  spots = random.Random(7).sample(rack.get_all_items(), 12)
  batches = plan_tip_inventory(spots, channels)
  per_column: dict = {}
  for spot in spots:
    per_column.setdefault(spot.get_absolute_location().x, []).append(spot)
  assert len(batches) == sum(math.ceil(len(c) / len(channels)) for c in per_column.values())
  assert sorted(s.name for b in batches for s in b.tip_spots) == sorted(s.name for s in spots)
  for batch in batches:
    assert len({spot.get_absolute_location().x for spot in batch.tip_spots}) == 1
    ys = [spot.get_absolute_location().y for spot in batch.tip_spots]
    assert ys == sorted(ys, reverse=True)
    assert batch.use_channels == sorted(channels)[: len(batch.tip_spots)]


def test_the_columns_go_in_ascending_x_and_rear_to_front_within_one():
  rack = _rack()
  spots = _spots(rack, "H3 A1 D3 C1 B3")
  batches = plan_tip_inventory(spots, [1, 0])
  assert [_names(b.tip_spots) for b in batches] == [
    ["rack_tipspot_A1", "rack_tipspot_C1"],
    ["rack_tipspot_B3", "rack_tipspot_D3"],
    ["rack_tipspot_H3"],
  ]
  assert [b.use_channels for b in batches] == [[0, 1], [0, 1], [0]]


def test_the_inventory_plan_parts_racks_at_different_deck_x():
  deck = Deck(size_x=1000, size_y=600, size_z=200)
  left, right = _rack("left"), _rack("right")
  deck.assign_child_resource(left, location=Coordinate(100, 100, 0))
  deck.assign_child_resource(right, location=Coordinate(300, 100, 0))
  batches = plan_tip_inventory([right.get_item("A1"), left.get_item("A1")], [0, 1])
  assert [_names(b.tip_spots) for b in batches] == [["left_tipspot_A1"], ["right_tipspot_A1"]]


def test_the_inventory_plan_refuses_a_spot_given_twice():
  rack = _rack()
  with pytest.raises(ValueError, match="given once"):
    plan_tip_inventory(_spots(rack, "A1 A1"), [0, 1])


def test_scattered_spots_are_probed_in_column_pairs_and_answered_in_the_order_given():
  rack = _rack()
  spots = _spots(rack, "H2 A1 C2 B1 E7")
  device = _Device(empty={rack.get_item("C2").name})
  found = _inventory(device, spots, [0, 1])
  assert list(found) == _names(spots)
  assert [found[s.name] for s in spots] == [True, True, False, True, True]
  picks = [names for kind, names, _ in device.calls if kind == "pick up"]
  assert picks == [
    ["rack_tipspot_A1", "rack_tipspot_B1"],
    ["rack_tipspot_C2", "rack_tipspot_H2"],
    ["rack_tipspot_E7"],
  ]
