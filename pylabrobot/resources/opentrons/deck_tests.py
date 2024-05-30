import textwrap
import unittest

from pylabrobot.resources.opentrons.deck import OTDeck
from pylabrobot.resources.opentrons.tip_racks import opentrons_96_tiprack_300ul
from pylabrobot.resources.corning_costar.plates import Cos_96_EZWash


class TestOTDeck(unittest.TestCase):
  """ Tests for the Opentrons deck. """
  def setUp(self) -> None:
    self.maxDiff = None

    self.deck = OTDeck()
    self.deck.assign_child_at_slot(opentrons_96_tiprack_300ul("tip_rack_1"), 7)
    self.deck.assign_child_at_slot(opentrons_96_tiprack_300ul("tip_rack_2"), 8)
    self.deck.assign_child_at_slot(opentrons_96_tiprack_300ul("tip_rack_3"), 9)
    self.deck.assign_child_at_slot(Cos_96_EZWash("my_plate"), 4)
    self.deck.assign_child_at_slot(Cos_96_EZWash("my_other_plate"), 5)

  def test_summary(self):
    self.assertEqual(self.deck.summary(), textwrap.dedent("""
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
    """))
