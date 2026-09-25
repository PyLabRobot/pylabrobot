"""PrepDeck: what the standard deck carries."""

import pytest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import PrepDeck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.trough import Trough


def test_teaching_spot_holds_the_teaching_needle():
  """The Prep's teaching needle is the STAR deck's: a closed probe that cannot pipette."""
  spot = PrepDeck().get_resource("teaching_tip")
  assert isinstance(spot, TipSpot)
  assert spot.make_tip().maximal_volume == 0


def test_liquid_waste_container_sits_in_the_waste_block_up_to_the_teaching_needle():
  """As wide as the waste block, its top level with the block's, its back edge at the teaching needle's front edge."""
  deck = PrepDeck()
  trough = deck.get_resource("liquid_waste_container")
  waste_block = deck.get_resource("waste_block")
  needle = deck.get_resource("teaching_tip")
  assert isinstance(trough, Trough)
  assert trough.parent is waste_block and needle.parent is waste_block
  trough_at, block_at, needle_at = (r.get_location_wrt(deck) for r in (trough, waste_block, needle))
  assert trough.get_absolute_size_x() == waste_block.get_absolute_size_x()
  assert trough_at.x == pytest.approx(block_at.x)
  assert trough_at.z + trough.get_absolute_size_z() == pytest.approx(
    block_at.z + waste_block.get_absolute_size_z()
  )
  assert trough_at.y + trough.get_absolute_size_y() == pytest.approx(needle_at.y)


@pytest.mark.parametrize("name_prefix", [None, "prep"])
def test_waste_positions_use_exact_component_names(name_prefix):
  """A missing waste site must not resolve to another resource with the same suffix."""
  deck = PrepDeck(name_prefix=name_prefix)
  names = ("waste_rear", "waste_front", "waste_mph")
  expected = {name: deck.get_resource(deck.get_component_name(name)) for name in names}
  assert deck.waste_positions == expected

  expected.pop("waste_rear").unassign()
  deck.assign_child_resource(
    Trash(name="other_waste_rear", size_x=6, size_y=6, size_z=0),
    location=Coordinate.zero(),
  )
  assert deck.waste_positions == expected

  deck.assign_child_resource(
    Resource(name=deck.get_component_name("waste_rear"), size_x=6, size_y=6, size_z=0),
    location=Coordinate.zero(),
  )
  assert deck.waste_positions == expected
