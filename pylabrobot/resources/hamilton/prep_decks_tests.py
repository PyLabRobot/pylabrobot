"""PrepDeck: what the standard deck carries."""

from pylabrobot.resources.hamilton import PrepDeck
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trough import Trough


def test_teaching_spot_holds_the_teaching_needle():
  """The Prep's teaching needle is the STAR deck's: a closed probe that cannot pipette."""
  spot = PrepDeck().get_resource("teaching_tip")
  assert isinstance(spot, TipSpot)
  assert spot.make_tip().maximal_volume == 0


def test_liquid_waste_container_sits_between_the_waste_block_and_the_teaching_needle():
  """As wide as the waste block, from its back edge to the teaching needle's front edge."""
  deck = PrepDeck()
  trough = deck.get_resource("liquid_waste_container")
  waste_block = deck.get_resource("waste_block")
  needle = deck.get_resource("teaching_tip")
  assert isinstance(trough, Trough)
  assert (
    trough.location is not None and waste_block.location is not None and needle.location is not None
  )
  assert trough.get_absolute_size_x() == waste_block.get_absolute_size_x()
  assert trough.location.x == waste_block.location.x
  assert trough.location.y == waste_block.location.y + waste_block.get_absolute_size_y()
  assert trough.location.y + trough.get_absolute_size_y() == needle.location.y
