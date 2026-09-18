"""PrepDeck: what the standard deck carries."""

import pytest

from pylabrobot.resources.hamilton import PrepDeck
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trough import Trough


def test_teaching_spot_holds_the_teaching_needle():
  """The Prep's teaching needle is the STAR deck's: a closed probe that cannot pipette."""
  spot = PrepDeck().teaching_tip_spot
  assert isinstance(spot, TipSpot)
  assert spot.make_tip().maximal_volume == 0


def test_liquid_waste_container_sits_in_the_waste_block_up_to_the_teaching_needle():
  """As wide as the waste block, its top level with the block's, its back edge at the teaching needle's front edge."""
  deck = PrepDeck()
  trough = deck.liquid_waste_container
  waste_block = deck.waste_block
  needle = deck.teaching_tip_spot
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
  """A deck names what it owns after itself, so two of them stand in one tree."""
  from pylabrobot.resources.coordinate import Coordinate
  from pylabrobot.resources.resource import Resource

  lab = Resource(name="lab", size_x=4000, size_y=2000, size_z=1000)
  first = PrepDeck(name="prep_left", with_core_grippers=True)
  second = PrepDeck(name="prep_right", with_core_grippers=True)
  lab.assign_child_resource(first, location=Coordinate.zero())
  lab.assign_child_resource(second, location=Coordinate(1000, 0, 0))

  names = [r.name for r in lab.get_all_children()]
  assert len(names) == len(set(names))
  assert "prep_left_spot_0_0" in names and "prep_right_spot_0_0" in names
  assert "prep_left_core_gripper_holder" in names and "prep_right_core_gripper_holder" in names
  for deck in (first, second):
    parts = (deck.teaching_tip_spot, deck.calibration_block, deck.waste_block)
    assert all(part is not None and part.name.startswith(deck.name) for part in parts)
    assert all(w.name.startswith(deck.name) for w in deck.waste_positions.values())
    assert all(s.name.startswith(deck.name) for s in deck.spots)


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

  # The parked tools are state, as they are on a STAR deck, so they are not saved with it.
  holder = read_back.core_gripper_holder
  assert holder is not None and holder.children == []
  parked = deck.core_gripper_holder
  assert parked is not None
  for tool in list(parked.children):
    tool.unassign()
  assert tree(read_back, read_back) == tree(deck, deck)

  assert [spot.name for spot in read_back.spots] == [spot.name for spot in deck.spots]
  assert read_back.waste_block is not None and read_back.calibration_block is not None
  assert read_back.teaching_tip_spot is not None and read_back.liquid_waste_container is not None
  assert list(read_back.waste_positions) == ["waste_rear", "waste_front", "waste_mph"]


def test_a_deck_built_without_its_parts_carries_none_of_them():
  """What `serialize` writes: a deck that builds nothing, for its children to be assigned to."""
  deck = PrepDeck(
    with_spots=False,
    with_calibration_block=False,
    with_waste_block=False,
    with_waste_positions=False,
  )
  assert deck.children == []
  assert deck.spots == [] and deck.waste_positions == {}
  assert deck.waste_block is None and deck.calibration_block is None
  assert deck.teaching_tip_spot is None and deck.liquid_waste_container is None
  assert deck.core_gripper_holder is None
