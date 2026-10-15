"""Tests for aligning one head with one tip rack, and for the tip commands planned on it."""

import math
from typing import List

import pytest

from pylabrobot.hamilton.star.resource_model.heads import head96
from pylabrobot.lib.liquid_handling.head_alignment import (
  get_items_under_channels,
  plan_drop,
  plan_pick_up,
)
from pylabrobot.resources import (
  Coordinate,
  Plate,
  Resource,
  Well,
  create_ordered_items_2d,
  set_tip_tracking,
)
from pylabrobot.resources.hamilton import (
  TIP_CAR_480_A00,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_raised_core_ii,
  hamilton_tip_300uL_filter,
)
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.tecan.plates import DeepWell_Greiner_1536_Well

HEAD, PITCH = (8, 12), 9.0


def _on_deck(grid: ItemizedResource, rotation: float = 0) -> Resource:
  """A deck with `grid` on it, turned by `rotation` degrees about z."""
  deck = Resource("deck", 1000, 1000, 0)
  grid.rotate(z=rotation)
  deck.assign_child_resource(grid, location=Coordinate(300, 300, 0))
  return deck


def _sbs_1536() -> Plate:
  """An SBS 1536 well plate: 32 x 48 wells at 2.25 mm."""
  return Plate(
    name="plate_1536",
    size_x=127.76,
    size_y=85.48,
    size_z=10.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=48,
      num_items_y=32,
      dx=8.25,
      dy=6.5,
      dz=1.0,
      item_dx=2.25,
      item_dy=2.25,
      size_x=1.5,
      size_y=1.5,
      size_z=8.0,
    ),
  )


def _physically_under(centres: List[Coordinate], channel, index) -> List[List[int]]:
  """The index of the item within 0.5 mm of each channel, from deck coordinates alone; -1 if none.

  Args:
    centres: each item's centre on the deck, in `get_all_items()` order.
    channel: the head channel, (row, column), placed over the item with `index`.
    index: the item the channel is placed over.
  """
  by_cell = {(round(p.x * 4), round(p.y * 4)): i for i, p in enumerate(centres)}
  anchor = centres[index]
  a1x, a1y = anchor.x - channel[1] * PITCH, anchor.y + channel[0] * PITCH
  rows = []
  for r in range(HEAD[0]):
    row = []
    for c in range(HEAD[1]):
      x, y = a1x + c * PITCH, a1y - r * PITCH
      # Look up the quarter-mm cell and its neighbours, then confirm the distance.
      near = [
        by_cell[k]
        for k in (
          (round(x * 4) + dx, round(y * 4) + dy) for dx in range(-2, 3) for dy in range(-2, 3)
        )
        if k in by_cell
      ]
      hits = [i for i in near if math.hypot(centres[i].x - x, centres[i].y - y) < 0.5]
      row.append(hits[0] if hits else -1)
    rows.append(row)
  return rows


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize(
  "make",
  [
    lambda: hamilton_96_tiprack_raised_core_ii("rack", make_tip=hamilton_tip_300uL_filter),
    _sbs_1536,
  ],
)
def test_the_item_under_each_channel_is_the_one_physically_there(make, rotation) -> None:
  grid = make()
  deck = _on_deck(grid, rotation)
  head = head96("head")
  items = grid.get_all_items()
  index_of = {id(item): i for i, item in enumerate(items)}
  centres = [item.get_location_wrt(deck, "c", "c", "b") for item in items]
  for channel, channel_rc in [("A1", (0, 0)), ("H12", (7, 11)), ("D6", (3, 5)), ("A12", (0, 11))]:
    for index in range(0, grid.num_items, 7):
      item = grid.get_child_identifier(items[index])
      under = get_items_under_channels(head, grid, channel, item)
      got = [[-1 if i is None else index_of[id(i)] for i in row] for row in under]
      assert got == _physically_under(centres, channel_rc, index), (channel, item)


def test_a_rack_turned_by_its_carrier_and_by_itself_is_turned_by_both() -> None:
  deck = Resource("deck", 2000, 2000, 0)
  carrier = TIP_CAR_480_A00("carrier")
  carrier.rotate(z=90)
  deck.assign_child_resource(carrier, location=Coordinate(500, 500, 0))
  rack = hamilton_96_tiprack_raised_core_ii("rack", make_tip=hamilton_tip_300uL_filter)
  rack.rotate(z=90)
  carrier[2] = rack
  # 90 + 90 degrees: channel H12 over spot A1 is the whole rack, H12's spot is A1, A1's is H12.
  under = get_items_under_channels(head96("head"), rack, "H12", "A1")
  assert under[7][11] is rack.get_item("A1") and under[0][0] is rack.get_item("H12")


def test_the_16_full_head_placements_on_a_1536_plate_visit_every_well_once() -> None:
  plate = _sbs_1536()
  head = head96("head")
  visits = {well.name: 0 for well in plate.get_all_items()}
  for anchor in ("A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"):
    for row_offset in (0, 2):
      well = f"{chr(ord(anchor[0]) + row_offset)}{anchor[1:]}"
      for row in get_items_under_channels(head, plate, "A1", well):
        for item in row:
          assert item is not None
          visits[item.name] += 1
  assert set(visits.values()) == {1}


def test_a_384_plate_takes_the_head_on_any_quadrant() -> None:
  plate = Plate(
    name="plate_384",
    size_x=127.76,
    size_y=85.48,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,
      num_items_y=16,
      dx=11.5,
      dy=8.0,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.5,
      size_y=3.5,
      size_z=11.0,
    ),
  )
  head = head96("head")
  for well in ("A1", "A2", "B1", "B2"):
    under = get_items_under_channels(head, plate, "A1", well)
    assert under[0][0] is plate.get_item(well)
    # Channel B2 stands two rows and two columns on.
    row, column = "AB".index(well[0]) + 2, int(well[1:]) + 1
    assert under[1][1] is plate.get_item((row, column))


def test_a_head_and_a_grid_whose_pitches_drift_apart_are_refused() -> None:
  # This 1536 plate's wells are 2.3 mm apart: channel 12 would stand 2.2 mm off its well.
  plate = DeepWell_Greiner_1536_Well("tecan")
  with pytest.raises(ValueError, match="2.20 mm off its item"):
    get_items_under_channels(head96("head"), plate, "A1", "A1")


def test_a_rack_not_square_to_the_deck_is_refused() -> None:
  rack = hamilton_96_tiprack_raised_core_ii("rack", make_tip=hamilton_tip_300uL_filter)
  _on_deck(rack, 5)
  with pytest.raises(ValueError, match="not square to the deck"):
    get_items_under_channels(head96("head"), rack, "A1", "A1")


def test_channels_past_the_rack_edge_stand_over_no_spot() -> None:
  rack = hamilton_96_tiprack_raised_core_ii("rack", make_tip=hamilton_tip_300uL_filter)
  # Channel B1 over spot A3: the head's row A stands behind the rack, columns 11 and 12 right of it.
  under = get_items_under_channels(head96("head"), rack, "B1", "A3")
  assert all(spot is None for spot in under[0])
  assert all(spot is None for row in under for spot in row[10:])
  assert under[1][0] is rack.get_item("A3")


@pytest.fixture(autouse=True)
def tracking():
  """Tip tracking on, as a drop into a rack needs; a test turns it off where it says so."""
  set_tip_tracking(True)
  yield
  set_tip_tracking(False)


def _world(framed: bool = False):
  """A deck with one rack on it, and a 96-head."""
  deck = Resource("deck", 1000, 1000, 0)
  rack = (
    hamilton_96_tiprack_300uL_filter("rack")
    if framed
    else hamilton_96_tiprack_raised_core_ii("rack", make_tip=hamilton_tip_300uL_filter)
  )
  deck.assign_child_resource(rack, location=Coordinate(300, 300, 0))
  return deck, rack, head96("head")


def _take(pairs) -> None:
  """Mount each pair's tip on its channel, as the head does once the device has picked them up."""
  for shaft, spot in pairs:
    shaft.mount_tip(spot.tip_for_pickup())


def _identifiers(head, rack, pairs):
  """Each pair as channel identifier: spot identifier."""
  return {head.get_child_identifier(s): rack.get_child_identifier(t) for s, t in pairs}


def test_a_partial_pick_up_pairs_the_channels_with_the_spots_under_them_and_a_drop_back_too():
  deck, rack, head = _world()
  rack.get_item("A2").unassign_tip()
  pairs = plan_pick_up(head, rack, "H12", "D6")
  # Channel H12 over spot D6: channels E7..H12 over spots A1..D6, except E8 over the empty A2.
  want = {f"{'EFGH'[r]}{c + 7}": f"{'ABCD'[r]}{c + 1}" for r in range(4) for c in range(6)}
  del want["E8"]
  assert _identifiers(head, rack, pairs) == want
  _take(pairs)
  assert _identifiers(head, rack, plan_drop(head, rack, "H12", "D6")) == want


def test_a_drop_with_one_tip_on_a_tip_is_refused():
  deck, rack, head = _world()
  _take(plan_pick_up(head, rack, "H12", "D6"))
  with pytest.raises(ValueError) as refused:
    plan_drop(head, rack, "H12", "D7")  # one column on: column 7 is still full
  assert str(refused.value) == (
    "drop refused: channels ['E12', 'F12', 'G12', 'H12'] would put a tip on a tip"
  )


def test_tips_beside_the_rack_a_frame_and_a_full_head_are_refused():
  deck, rack, head = _world()
  _take(plan_pick_up(head, rack, "H12", "D6"))
  with pytest.raises(ValueError, match="beside"):
    plan_drop(head, rack, "H12", "A1")
  with pytest.raises(ValueError, match="already carry"):
    plan_pick_up(head, rack, "A1", "A1")

  deck, rack, head = _world(framed=True)
  assert len(plan_pick_up(head, rack, "A1", "A1")) == 96
  with pytest.raises(ValueError, match="frame"):
    plan_pick_up(head, rack, "H12", "D6")


def test_every_problem_is_named_in_one_refusal():
  deck, rack, head = _world(framed=True)
  _take(plan_pick_up(head, rack, "A1", "A1"))
  with pytest.raises(ValueError) as refused:
    plan_pick_up(head, rack, "H12", "D6")
  assert "come down on the frame" in str(refused.value)
  assert "already carry a tip" in str(refused.value)
  assert "no channel stands over a tip" in str(refused.value)


def test_an_untracked_spot_gives_a_tip_whatever_the_model_holds():
  deck, rack, head = _world()
  for spot in ("A1", "B1"):
    rack.get_item(spot).unassign_tip()
  rack.get_item("B1").disable_tip_tracking()
  taken = _identifiers(head, rack, plan_pick_up(head, rack, "A1", "A1"))
  assert "A1" not in taken and taken["B1"] == "B1" and len(taken) == 95


def test_a_drop_into_an_untracked_rack_is_refused_but_a_pick_up_from_it_is_not():
  deck, rack, head = _world()
  set_tip_tracking(False)
  pairs = plan_pick_up(head, rack, "A1", "A1")
  assert len(pairs) == 96
  _take(pairs)
  with pytest.raises(ValueError, match="needs tip tracking"):
    plan_drop(head, rack, "A1", "A1")


def test_a_rack_without_a_declared_frame_height_is_refused_only_when_a_channel_is_past_its_edge():
  deck, rack, head = _world()
  rack._frame_height = None
  assert len(plan_pick_up(head, rack, "A1", "A1")) == 96  # the whole rack: frame irrelevant
  with pytest.raises(ValueError, match="frame_height is not defined"):
    plan_pick_up(head, rack, "H12", "D6")
