import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import (
  TIP_CAR_288_C00,
  TIP_CAR_480_A00,
  STARDeck,
  hamilton_96_tiprack_1000uL,
)


class StandardTipCarrierTests(unittest.TestCase):
  def test_rack_is_centered_over_the_opening(self):
    for carrier_fn, rotation, opening in [
      (TIP_CAR_480_A00, 0, (114.5, 74.0)),
      (TIP_CAR_288_C00, 90, (74.0, 114.5)),
    ]:
      with self.subTest(carrier=carrier_fn.__name__):
        carrier = carrier_fn("carrier")
        carrier[0] = rack = hamilton_96_tiprack_1000uL(name="rack").rotated(z=rotation)
        site = carrier.sites[0]
        self.assertEqual((site.get_size_x(), site.get_size_y()), opening)
        rack_center = rack.get_absolute_location("c", "c")
        site_center = site.get_absolute_location("c", "c")
        self.assertAlmostEqual(rack_center.x, site_center.x)
        self.assertAlmostEqual(rack_center.y, site_center.y)

  def test_tip_spot_positions_on_star_deck(self):
    # positions whose firmware commands match Venus
    for carrier_fn, rotation, a1, h12 in [
      (TIP_CAR_480_A00, 0, Coordinate(117.9, 145.8, 216.45), Coordinate(216.9, 82.8, 216.45)),
      (TIP_CAR_288_C00, 90, Coordinate(113.5, 111.0, 216.2), Coordinate(176.5, 210.0, 216.2)),
    ]:
      with self.subTest(carrier=carrier_fn.__name__):
        deck = STARDeck()
        carrier = carrier_fn("carrier")
        carrier[0] = rack = hamilton_96_tiprack_1000uL(name="rack").rotated(z=rotation)
        deck.assign_child_resource(carrier, track=1)
        for spot, expected in (("A1", a1), ("H12", h12)):
          actual = rack.get_item(spot).get_absolute_location("c", "c", "b")
          for axis in ("x", "y", "z"):
            self.assertAlmostEqual(getattr(actual, axis), getattr(expected, axis))
