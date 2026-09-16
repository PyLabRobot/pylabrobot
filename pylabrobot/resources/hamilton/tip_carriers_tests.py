import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import (
  TIP_CAR_288_C00,
  TIP_CAR_480_A00,
  STARDeck,
  TIP_CAR_72_4mlTF_C00,
  TIP_CAR_96BC_5mlT_A00,
  hamilton_24_tiprack_4000uL_filter,
  hamilton_24_tiprack_5000uL,
  hamilton_96_tiprack_10uL_NTR,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL_NTR,
  hamilton_96_tiprack_1000uL,
  hamilton_tip_carrier_L5_ntr_a00,
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

  def test_non_embedded_racks_on_their_carriers(self):
    # TODO: model the 4 mL / 5 mL racks, then place them like the standard rack
    for carrier_fn, rack_fn in [
      (TIP_CAR_72_4mlTF_C00, hamilton_24_tiprack_4000uL_filter),
      (TIP_CAR_96BC_5mlT_A00, hamilton_24_tiprack_5000uL),
    ]:
      with self.subTest(carrier=carrier_fn.__name__):
        carrier = carrier_fn("carrier")
        carrier[0] = rack = rack_fn("rack")
        self.assertEqual(rack.location, Coordinate.zero())


class NestedTipCarrierTests(unittest.TestCase):
  def test_tip_spot_positions_on_star_deck(self):
    # C0TP of run10 in bct_re 2603_STAR_tippickup/260916_tippickup_testing:
    # hamilton_tip_carrier_L5_ntr_a00 at x 752.5 with the 10, 50 and 300 uL racks on sites 5, 4 and
    # 3, all picked up at z 184.0
    for rack_fn, site, a1_y in [
      (hamilton_96_tiprack_10uL_NTR, 0, 145.8),
      (hamilton_96_tiprack_50uL_NTR, 1, 241.8),
      (hamilton_96_tiprack_300uL_NTR, 2, 337.8),
    ]:
      with self.subTest(rack=rack_fn.__name__):
        deck = STARDeck()
        carrier = hamilton_tip_carrier_L5_ntr_a00("carrier")
        carrier[site] = rack = rack_fn("rack")
        deck.assign_child_resource(carrier, location=Coordinate(752.5, 63, 100))
        for spot, expected in (
          ("A1", Coordinate(770.4, a1_y, 184.0)),
          ("H12", Coordinate(869.4, a1_y - 63, 184.0)),
        ):
          actual = rack.get_item(spot).get_absolute_location("c", "c", "b")
          for axis in ("x", "y", "z"):
            self.assertAlmostEqual(getattr(actual, axis), getattr(expected, axis))

  def test_tips_end_where_venus_puts_the_tip_container(self):
    # Cntr.1.base of LT_L_NE_stack, TIP_50ul_L_NE_stack and ST_L_NE_stack, above the rack's bottom
    for rack_fn, container_base in [
      (hamilton_96_tiprack_10uL_NTR, 31.0),
      (hamilton_96_tiprack_50uL_NTR, 12.6),
      (hamilton_96_tiprack_300uL_NTR, 3.0),
    ]:
      with self.subTest(rack=rack_fn.__name__):
        rack = rack_fn("rack")
        tip = rack.get_item("A1").get_tip()
        self.assertAlmostEqual(tip.get_location_wrt(rack).z, container_base, delta=0.15)

  def test_a_nested_rack_stands_on_the_one_below(self):
    carrier = hamilton_tip_carrier_L5_ntr_a00("carrier")
    carrier[0] = bottom = hamilton_96_tiprack_50uL_NTR("bottom")
    top = hamilton_96_tiprack_50uL_NTR("top")
    bottom.assign_child_resource(top)
    self.assertEqual(
      top.get_item("A1").get_absolute_location("c", "c", "b"),
      bottom.get_item("A1").get_absolute_location("c", "c", "b") + Coordinate(0, 0, 16),
    )
