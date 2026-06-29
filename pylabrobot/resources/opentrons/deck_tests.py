import textwrap
import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.plates import (
  cor_96_wellplate_360uL_Fb,
)
from pylabrobot.resources.opentrons.deck import OTDeck
from pylabrobot.resources.opentrons.tip_racks import (
  opentrons_96_tiprack_300ul,
)
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder


class TestOTDeck(unittest.TestCase):
  """Tests for the Opentrons deck."""

  def setUp(self) -> None:
    self.maxDiff = None

    self.deck = OTDeck()
    self.deck.assign_child_at_slot(opentrons_96_tiprack_300ul("tip_rack_1"), 7)
    self.deck.assign_child_at_slot(opentrons_96_tiprack_300ul("tip_rack_2"), 8)
    self.deck.assign_child_at_slot(opentrons_96_tiprack_300ul("tip_rack_3"), 9)
    self.deck.assign_child_at_slot(cor_96_wellplate_360uL_Fb("my_plate"), 4)
    self.deck.assign_child_at_slot(cor_96_wellplate_360uL_Fb("my_other_plate"), 5)

  def test_slot_locations_inset_from_plate_corner(self):
    """Slots are re-based onto the deck plate corner, so slot 1 sits at the corner offset
    (115.65, 68.03) rather than the deck origin, and every other slot shifts by the same amount."""
    self.assertEqual(self.deck.slot_locations[0], Coordinate(115.65, 68.03, 0))
    self.assertEqual(self.deck.slot_locations[11], Coordinate(380.65, 339.53, 0))

  def test_slots_are_resource_holders_at_inset_positions(self):
    """The 12 slots are ResourceHolder children at the inset slot locations, and a slot's labware
    is that holder's child."""
    holders = self.deck._slot_holders
    self.assertEqual(len(holders), 12)
    self.assertTrue(all(isinstance(h, ResourceHolder) for h in holders))
    self.assertEqual(holders[0].location, Coordinate(115.65, 68.03, 0))
    self.assertIs(holders[6].resource, self.deck.slots[6])  # tip_rack_1 lives in slot 7's holder

  def test_assign_child_resource_rejects_direct_labware(self):
    """Labware must enter a slot via assign_child_at_slot; assigning it directly to the deck (as a
    deck serialized before slots became holders would) is rejected rather than misplaced."""
    deck = OTDeck()
    plate = cor_96_wellplate_360uL_Fb("p")
    with self.assertRaises(ValueError):
      deck.assign_child_resource(plate, location=Coordinate(115.65, 68.03, 0))

  def test_serialize_deserialize_round_trip(self):
    """A deck with labware survives serialize -> deserialize, with slots resolving to the same
    labware (validates replacing the placeholder holders with the loaded ones)."""
    loaded = Resource.deserialize(self.deck.serialize())
    assert isinstance(loaded, OTDeck)
    self.assertEqual(loaded.slots[6].name, "tip_rack_1")
    self.assertEqual(loaded.get_slot(loaded.slots[6]), 7)
    self.assertEqual(loaded.slots[3].name, "my_plate")
    self.assertEqual(loaded.slot_locations[0], Coordinate(115.65, 68.03, 0))

  def test_summary(self):
    self.assertEqual(
      self.deck.summary(),
      textwrap.dedent(
        """
      Deck: 624.3mm x 565.2mm

      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      | 10: Empty       | 11: Empty       | 12: trash_co... |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      |  7: tip_rack_1  |  8: tip_rack_2  |  9: tip_rack_3  |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      |  4: my_plate    |  5: my_other... |  6: Empty       |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      |  1: Empty       |  2: Empty       |  3: Empty       |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
    """
      ),
    )
