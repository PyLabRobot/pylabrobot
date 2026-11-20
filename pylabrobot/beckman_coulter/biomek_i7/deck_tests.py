import unittest

from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck, biomek_x, biomek_y
from pylabrobot.beckman_coulter.biomek_i7.resource_model import biomek_static_1x3, cm
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck
from pylabrobot.resources import beckman_coulter_biomek_96_tiprack_1070uL


class BiomekI7DeckTests(unittest.TestCase):
  def setUp(self):
    self.deck = generic_deck()

  def test_the_generic_deck_has_biomeks_positions(self):
    # Deck1 of the instrument file the generic configuration was made from.
    logged = {f"P{i}" for i in range(1, 25)} | {f"TL{i}" for i in (1, 2, 3, 4, 5, 6, 10, 11)}
    logged |= {"TR1", "TR2", "W2", "W3", "BS1", "CP1", "TA1"}
    self.assertEqual(set(self.deck.position_names), logged)

  def test_positions_land_where_biomek_has_them(self):
    # The instrument file's Decks/Deck1/Positions/<name>: X1, Y1, the position's back left corner
    # in cm, within 1.1 mm: the per-ALP calibration the file carries beyond the pin grid.
    expected = {
      "P1": (40.4144, 25.8845),
      "TR1": (10.9473, 17.5792),
      "W2": (142.2654, 56.9271),
      "W3": (78.6271, 14.3213),
      "TL3": (11.2511, 57.1587),
    }
    for name, (x1, y1) in expected.items():
      with self.subTest(position=name):
        site = self.deck.get_position(name)
        loc = site.get_location_wrt(self.deck)
        self.assertAlmostEqual(biomek_x(loc.x), cm(x1), delta=1.1)
        self.assertAlmostEqual(biomek_y(loc.y + site.get_size_y()), cm(y1), delta=1.1)

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

  def test_a_tip_box_on_a_tip_load_position(self):
    deck = BiomekI7Deck()
    deck.assign_alp(biomek_static_1x3("s", ["P1", "P2", "P3"]), column=20, row=30)
    rack = beckman_coulter_biomek_96_tiprack_1070uL("tips")
    deck.get_position("P1").assign_child_resource(rack)
    a1 = rack.get_item("A1")
    centre = a1.get_location_wrt(rack) + a1.center()
    # BC1070: well1xoffset 1.44 cm, well1yoffset 1.12 cm from the back, height 11.41 cm.
    self.assertAlmostEqual(centre.x, 14.4)
    self.assertAlmostEqual(rack.get_size_y() - centre.y, 11.2)
    self.assertAlmostEqual(centre.z, 114.1)


if __name__ == "__main__":
  unittest.main()
