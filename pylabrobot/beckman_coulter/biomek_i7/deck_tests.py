import unittest

from pylabrobot.beckman_coulter.biomek_i7.resource_model import biomek_static_1x3
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck


class BiomekI7DeckTests(unittest.TestCase):
  def setUp(self):
    self.deck = generic_deck()

  def test_biomek_front_is_plr_front(self):
    # Biomek's Y grows towards the front: row 30 is the front row, row 10 the back one.
    front = self.deck.get_position("P4").get_location_wrt(self.deck)
    back = self.deck.get_position("TL10").get_location_wrt(self.deck)
    self.assertLess(front.y, back.y)

  def test_an_alps_first_site_is_at_its_back(self):
    alp = biomek_static_1x3("s", ["a", "b", "c"])
    a, c = alp.get_site("A1").location, alp.get_site("A3").location
    assert a is not None and c is not None
    self.assertGreater(a.y, c.y)


if __name__ == "__main__":
  unittest.main()
