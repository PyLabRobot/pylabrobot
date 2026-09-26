"""PrepDeck: what the standard deck carries."""

from typing import List

import pytest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import PrepDeck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trough import Trough


def test_teaching_spot_holds_the_teaching_needle():
  """The Prep's teaching needle is the STAR deck's: a closed probe that cannot pipette, and it
  stands in its spot from the start, as the needles stand in a STAR deck's rack."""
  spot = PrepDeck().teaching_needle_spot
  assert spot is not None and spot.has_tip()
  assert spot.get_tip().model == "hamilton_teaching_needle_300uL"
  assert isinstance(spot, TipSpot)
  assert spot.make_tip().maximal_volume == 0


def test_liquid_waste_container_sits_in_the_waste_block_up_to_the_teaching_needle():
  """As wide as the waste block, its top level with the block's, its back edge at the teaching needle's front edge."""
  deck = PrepDeck()
  trough = deck.liquid_waste_container
  waste_block = deck.waste_block
  needle = deck.teaching_needle_spot
  assert isinstance(trough, Trough) and waste_block is not None and needle is not None
  assert trough.parent is waste_block and needle.parent is waste_block
  trough_at, block_at, needle_at = (r.get_location_wrt(deck) for r in (trough, waste_block, needle))
  assert trough.get_absolute_size_x() == waste_block.get_absolute_size_x()
  assert trough_at.x == pytest.approx(block_at.x)
  assert trough_at.z + trough.get_absolute_size_z() == pytest.approx(
    block_at.z + waste_block.get_absolute_size_z()
  )
  assert trough_at.y + trough.get_absolute_size_y() == pytest.approx(needle_at.y)


def test_two_decks_stand_in_one_tree():
  """A deck names what it owns after its prefix, so two of them stand in one tree."""
  from pylabrobot.resources.coordinate import Coordinate
  from pylabrobot.resources.resource import Resource

  lab = Resource(name="lab", size_x=4000, size_y=2000, size_z=1000)
  first = PrepDeck(name="prep_left_deck", name_prefix="prep_left", with_core_grippers=True)
  second = PrepDeck(name="prep_right_deck", name_prefix="prep_right", with_core_grippers=True)
  lab.assign_child_resource(first, location=Coordinate.zero())
  lab.assign_child_resource(second, location=Coordinate(1000, 0, 0))

  names = [r.name for r in lab.get_all_children()]
  assert len(names) == len(set(names))
  assert "prep_left_spot_0_0" in names and "prep_right_spot_0_0" in names
  assert "prep_left_core_grippers" in names and "prep_right_core_grippers" in names
  for deck, prefix in ((first, "prep_left"), (second, "prep_right")):
    parts = (deck.teaching_needle_spot, deck.calibration_block, deck.waste_block)
    assert all(part is not None and part.name.startswith(prefix) for part in parts)
    assert all(w.name.startswith(prefix) for w in deck.waste_positions.values())
    assert all(s.name.startswith(prefix) for s in deck.spots)


def test_a_saved_deck_reads_back_as_the_deck_it_was():
  """Saving encodes the parts as children, so reading builds none of them again, and what the
  deck carries is found among its children rather than held from when they were built."""
  from pylabrobot.resources.deck import Deck

  deck = PrepDeck(with_core_grippers=True)
  read_back = Deck.deserialize(deck.serialize())
  assert isinstance(read_back, PrepDeck)

  def tree(resource, of):
    return (
      resource.name,
      type(resource).__name__,
      None if resource is of else resource.get_location_wrt(of),
      [tree(child, of) for child in resource.children],
    )

  # What a channel can pick up - the parked tools, the teaching needle - is state, as it is on a
  # STAR deck, so it is not saved with the deck. Everything else comes back as it was.
  from pylabrobot.resources.head_tool import HeadTool

  holder = read_back.core_gripper_holder
  assert holder is not None and holder.children == []
  assert read_back.teaching_needle_spot is not None
  assert not read_back.teaching_needle_spot.has_tip()
  for resource in list(deck.get_all_children()):
    if isinstance(resource, HeadTool):
      resource.unassign()
  assert tree(read_back, read_back) == tree(deck, deck)

  assert [spot.name for spot in read_back.spots] == [spot.name for spot in deck.spots]
  assert read_back.waste_block is not None and read_back.calibration_block is not None
  assert read_back.teaching_needle_spot is not None and read_back.liquid_waste_container is not None
  assert read_back.waste_bin is not None
  assert list(read_back.waste_positions) == ["waste_rear", "waste_front", "waste_mph"]


def test_a_deck_built_without_its_parts_carries_none_of_them():
  """What `serialize` writes: a deck that builds nothing, for its children to be assigned to."""
  deck = PrepDeck(
    with_spots=False,
    with_calibration_block=False,
    with_waste_block=False,
    with_waste_bin=False,
    with_waste_positions=False,
  )
  assert deck.children == []
  assert deck.spots == [] and deck.waste_positions == {}
  assert deck.waste_block is None and deck.calibration_block is None
  assert deck.teaching_needle_spot is None and deck.liquid_waste_container is None
  assert deck.core_gripper_holder is None and deck.waste_bin is None


def test_the_holders_stand_where_they_were_probed_and_the_waste_block_does_not_move():
  """The OBJ-derived parts sit at their measured places; what the device anchors is untouched."""
  deck = PrepDeck()
  first, last = deck[0].get_location_wrt(deck, "c", "c"), deck[7].get_location_wrt(deck, "c", "c")
  assert (first.x, first.y) == pytest.approx((62.55, 44.23))
  assert (last.x, last.y) == pytest.approx((202.55, 329.23))

  block = deck.calibration_block
  assert block is not None
  top = block.get_location_wrt(deck, "c", "c", "t")
  assert (top.x, top.y, top.z) == pytest.approx((132.55, 186.73, 22.0))

  waste_block = deck.waste_block
  assert waste_block is not None
  assert waste_block.get_location_wrt(deck) == Coordinate(282.25, -4.25, 0.0)
  tool = deck.get_resource("core_grippers_back").get_location_wrt(deck, "c", "c")
  assert (tool.x, tool.y) == pytest.approx((290.0, 275.577))


def test_the_waste_bin_stands_beside_the_waste_block_below_its_top():
  """Left face on the block's right face, front 35 mm in front of it, top 4 mm below its top."""
  deck = PrepDeck()
  block, waste_bin = deck.waste_block, deck.waste_bin
  assert block is not None and waste_bin is not None
  assert waste_bin.parent is deck and waste_bin.model == "hamilton_prep_waste_bin"
  block_rbt = block.get_location_wrt(deck, "r", "f", "t")
  bin_lft = waste_bin.get_location_wrt(deck, "l", "f", "t")
  assert bin_lft.x == pytest.approx(block_rbt.x)
  assert bin_lft.y == pytest.approx(block_rbt.y - 35.0)
  assert bin_lft.z == pytest.approx(block_rbt.z - 4.0)


def test_the_safe_deck_height_follows_the_longest_tip_when_asked(monkeypatch):
  deck = PrepDeck()
  assert deck.safe_deck_height == 75.0
  checked: List[Resource] = []
  monkeypatch.setattr(deck, "_check_safe_deck_height", checked.append)
  # Only the teaching needle, as long as a 300 uL tip: 167.5 - 51.9 - 5.
  assert deck.update_safe_deck_height_from_tips(167.5) == 110.6
  assert deck.safe_deck_height == 110.6
  assert checked == list(deck.children)
