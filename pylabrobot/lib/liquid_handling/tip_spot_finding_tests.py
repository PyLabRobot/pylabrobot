"""Tests for tip spot selection from tracked tip inventory."""

import random
from itertools import groupby
from typing import List, Optional, Tuple

import pytest

from pylabrobot.lib.liquid_handling.tip_spot_finding import find_tip_spots
from pylabrobot.resources import TIP_CAR_480_A00, Coordinate, Resource, TipRack, TipSpot
from pylabrobot.resources.hamilton import (
  hamilton_96_tiprack_50uL,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_1000uL_filter,
)


def _racks(occupied: List[List[str]], x_offsets: List[float]) -> Tuple[Resource, List[TipRack]]:
  """Racks on one root, each with tips at the given well identifiers."""
  root = Resource("root", size_x=1000, size_y=1000, size_z=100)
  racks: List[TipRack] = []
  for index, (wells, x) in enumerate(zip(occupied, x_offsets)):
    rack = hamilton_96_tiprack_300uL_filter(f"rack_{index}")
    rack.set_tip_state({spot.get_identifier(): False for spot in rack.get_all_items()})
    rack.set_tip_state({well: True for well in wells})
    root.assign_child_resource(rack, location=Coordinate(x, 0, 0))
    racks.append(rack)
  return root, racks


def _ids(spots: List[TipSpot]) -> List[str]:
  """Spot names, which carry the rack name and well identifier."""
  return [spot.name for spot in spots]


FULL = [f"{row}{column}" for column in range(1, 13) for row in "ABCDEFGH"]


def test_rack_order_and_column_order() -> None:
  """Racks with an empty spot first, fewest tips left, then left-most; columns back to front."""
  root, racks = _racks([FULL, FULL[:40], FULL[1:31], FULL[:40]], [0, 400, 600, 200])
  spots = find_tip_spots(root, has_tip=True)
  order = [spots[i].parent for i in (0, 30, 70, 110)]
  assert order == [racks[2], racks[3], racks[1], racks[0]]
  assert _ids(spots[:8]) == [f"rack_2_tipspot_{row}1" for row in "BCDEFGH"] + ["rack_2_tipspot_A2"]


def test_volume_and_filter_follow_has_tip() -> None:
  """Empty spots match by their configured tip only when has_tip is False."""
  root, racks = _racks([FULL[:8]], [0])
  assert len(find_tip_spots(root, volume=300)) == 8
  assert len(find_tip_spots(root, has_tip=False, volume=300)) == 88
  assert len(find_tip_spots(root, has_filter=True)) == 8
  assert find_tip_spots(root, volume=50) == []
  assert find_tip_spots(root, has_tip=False, has_filter=False) == []


def test_count_is_exact_or_empty() -> None:
  """count returns that many spots, or none when fewer match."""
  root, racks = _racks([FULL[:5]], [0])
  assert len(find_tip_spots(root, has_tip=True, count=5)) == 5
  assert find_tip_spots(root, has_tip=True, count=6) == []
  with pytest.raises(ValueError):
    find_tip_spots(root, count=0)


def test_count_across_racks_is_sorted_back_to_front() -> None:
  """A batch crossing into the next rack is re-sorted by descending y."""
  root, racks = _racks([["G12", "H12"], FULL], [0, 200])
  spots = find_tip_spots(root, has_tip=True, count=8)
  assert {spot.parent for spot in spots} == set(racks)
  ys = [spot.get_absolute_location(y="c").y for spot in spots]
  assert ys == sorted(ys, reverse=True)


def test_x_aligned_skips_short_column() -> None:
  """A partial column too short for count is skipped for the next full one."""
  root, racks = _racks([FULL[5:]], [0])
  spots = find_tip_spots(root, has_tip=True, count=8, x_aligned=True)
  assert _ids(spots) == [f"rack_0_tipspot_{row}2" for row in "ABCDEFGH"]
  assert len(find_tip_spots(root, has_tip=True, x_aligned=True)) == 3


def test_x_aligned_does_not_merge_racks_sharing_x() -> None:
  """A rack's last column and another rack's first column at the same x stay separate."""
  root, racks = _racks([["E12", "F12", "G12", "H12"], FULL], [0, 99])
  assert racks[0]["E12"][0].get_absolute_location(x="c").x == pytest.approx(
    racks[1]["A1"][0].get_absolute_location(x="c").x
  )
  spots = find_tip_spots(root, has_tip=True, count=8, x_aligned=True)
  assert _ids(spots) == [f"rack_1_tipspot_{row}1" for row in "ABCDEFGH"]


def test_x_aligned_falls_back_to_unaligned_batch() -> None:
  """With no column long enough, the cross-column batch is returned."""
  root, racks = _racks([["A1", "B1", "C1", "D1", "A2", "B2", "C2", "D2"]], [0])
  assert len(find_tip_spots(root, has_tip=True, count=8, x_aligned=True)) == 8


def test_rotated_rack_follows_deck_axes() -> None:
  """A rack turned 180 degrees is consumed by deck x and y, from its column 12 and row H."""
  root = Resource("root", size_x=1000, size_y=1000, size_z=100)
  rack = hamilton_96_tiprack_300uL_filter("rack_0")
  rack.rotate(z=180)
  root.assign_child_resource(rack, location=Coordinate(500, 500, 0))
  spots = find_tip_spots(root, has_tip=True, count=8, x_aligned=True)
  assert _ids(spots) == [f"rack_0_tipspot_{row}12" for row in "HGFEDCBA"]


def _reference_find_tip_spots(
  tip_racks: List[TipRack],
  has_tip: Optional[bool],
  volume: Optional[float],
  has_filter: Optional[bool],
  count: Optional[int],
  x_aligned: bool,
) -> List[TipSpot]:
  """The consumption order computed directly: one sort of every spot on absolute coordinates."""

  def matches(s: TipSpot) -> bool:
    if has_tip is not None and s.has_tip() != has_tip:
      return False
    if volume is None and has_filter is None:
      return True
    tip = s.make_tip() if has_tip is False else (s.get_tip() if s.has_tip() else None)
    return (
      tip is not None
      and (volume is None or tip.nominal_volume == volume)
      and (has_filter is None or tip.has_filter == has_filter)
    )

  def rank(i: int, rack: TipRack) -> Tuple[bool, int, float, float, int]:
    left = sum(s.has_tip() for s in rack.get_all_items())
    centre = rack.get_absolute_location(x="c", y="c")
    return (left == 96, left, round(centre.x, 3), -centre.y, i)

  ranks = [rank(i, rack) for i, rack in enumerate(tip_racks)]
  spots = sorted(
    (
      (
        ranks[i],
        round(s.get_absolute_location(x="c").x, 3),
        -s.get_absolute_location(y="c").y,
        s,
      )
      for i, rack in enumerate(tip_racks)
      for s in rack.get_all_items()
      if matches(s)
    ),
    key=lambda item: item[:3],
  )
  if x_aligned:
    for _, column_items in groupby(spots, key=lambda item: item[:2]):
      column = [item[3] for item in column_items]
      if count is None:
        return column
      if len(column) >= count:
        return column[:count]
  if count is None:
    return [item[3] for item in spots]
  if len(spots) < count:
    return []
  return [item[3] for item in sorted(spots[:count], key=lambda item: item[2])]


@pytest.mark.parametrize("seed", range(200))
def test_matches_reference_on_random_decks(seed: int) -> None:
  """Random racks, fills, positions and rotations give the reference order for every query."""
  rng = random.Random(seed)
  root = Resource("root", size_x=3000, size_y=3000, size_z=100)
  rack_types = [
    hamilton_96_tiprack_50uL,
    hamilton_96_tiprack_300uL_filter,
    hamilton_96_tiprack_1000uL_filter,
  ]
  racks: List[TipRack] = []
  for index in range(rng.randint(1, 4)):
    rack = rng.choice(rack_types)(f"rack_{index}")
    fill = rng.choice([0.0, 0.1, 0.5, 0.9, 1.0])
    rack.set_tip_state([rng.random() < fill for _ in range(96)])
    rack.rotate(z=rng.choice([0, 0, 90, 180, 270]))
    x = 500 + 99 * index * rng.choice([1, 2, 3])
    root.assign_child_resource(rack, location=Coordinate(x, 500 + rng.choice([0, 150]), 0))
    racks.append(rack)

  for _ in range(5):
    has_tip = rng.choice([None, True, False])
    volume = rng.choice([None, 50.0, 300.0, 1000.0])
    has_filter = rng.choice([None, True, False])
    count = rng.choice([None, 1, 4, 8])
    x_aligned = rng.choice([False, True])
    query = (has_tip, volume, has_filter, count, x_aligned)
    assert find_tip_spots(root, *query) == _reference_find_tip_spots(racks, *query), query


def test_root_can_be_any_level_of_the_tree() -> None:
  """A single rack, a carrier or a facility above several benches all work as the root."""
  facility = Resource("facility", size_x=5000, size_y=5000, size_z=100)
  bench = Resource("bench", size_x=2000, size_y=1000, size_z=100)
  facility.assign_child_resource(bench, location=Coordinate(1000, 0, 0))
  loose = hamilton_96_tiprack_300uL_filter("loose")
  loose.set_tip_state({"A1": False})
  bench.assign_child_resource(loose, location=Coordinate(0, 0, 0))
  carrier = TIP_CAR_480_A00("carrier")
  bench.assign_child_resource(carrier, location=Coordinate(500, 0, 0))
  on_carrier = hamilton_96_tiprack_300uL_filter("on_carrier")
  carrier[0] = on_carrier
  other_bench_rack = hamilton_96_tiprack_300uL_filter("other_bench_rack")
  facility.assign_child_resource(other_bench_rack, location=Coordinate(4000, 0, 0))

  assert _ids(find_tip_spots(loose, has_tip=True, count=1)) == ["loose_tipspot_B1"]
  assert _ids(find_tip_spots(carrier, has_tip=True, count=1)) == ["on_carrier_tipspot_A1"]
  assert {spot.parent for spot in find_tip_spots(bench, has_tip=True)} == {loose, on_carrier}
  spots = find_tip_spots(facility, has_tip=True)
  assert [spots[0].name, spots[95].name, spots[-1].name] == [
    "loose_tipspot_B1",
    "on_carrier_tipspot_A1",
    "other_bench_rack_tipspot_H12",
  ]
  assert find_tip_spots(Resource("empty", size_x=1, size_y=1, size_z=1), count=1) == []


@pytest.mark.parametrize("turn", [90, 180, 270])
def test_order_is_in_the_root_frame(turn: int) -> None:
  """Turning the resource above root leaves root's order unchanged; the topmost frame would not."""

  def build(facility_turn: int) -> Resource:
    facility = Resource("facility", size_x=5000, size_y=5000, size_z=100)
    device = Resource("device", size_x=1500, size_y=700, size_z=100)
    device.rotate(z=facility_turn)
    facility.assign_child_resource(device, location=Coordinate(2500, 2500, 0))
    deck = Resource("deck", size_x=1500, size_y=700, size_z=100)
    device.assign_child_resource(deck, location=Coordinate(0, 0, 0))
    for index, x in enumerate([100, 300]):
      rack = hamilton_96_tiprack_300uL_filter(f"rack_{index}")
      rack.set_tip_state([i % 3 != 0 for i in range(96)])
      deck.assign_child_resource(rack, location=Coordinate(x, 100, 0))
    return deck

  upright, turned = build(0), build(turn)
  for count, x_aligned in [(None, False), (5, False), (2, True)]:
    expected = find_tip_spots(upright, has_tip=True, count=count, x_aligned=x_aligned)
    found = find_tip_spots(turned, has_tip=True, count=count, x_aligned=x_aligned)
    assert expected and _ids(found) == _ids(expected)
