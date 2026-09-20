import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import (
  TIP_CAR_288_C00,
  TIP_CAR_480_A00,
  TIP_CAR_480BC_A00,
  TIP_CAR_NTR_A00,
  STARDeck,
  TIP_CAR_72_4mlTF_C00,
  TIP_CAR_96BC_5mlT_A00,
  hamilton_24_tiprack_4000uL_filter,
  hamilton_24_tiprack_5000uL,
  hamilton_96_tiprack_10uL,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL,
  hamilton_96_tiprack_1000uL,
  hamilton_96_tiprack_raised_core_i,
  hamilton_96_tiprack_raised_core_ii,
  hamilton_mfx_carrier_L5_base,
  hamilton_mfx_module_tiprackholder_ntr,
  hamilton_mfx_tiprackholder_standard,
  hamilton_tip_10uL,
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

  def test_tip_spot_positions_on_mfx_tip_module(self):
    for rack_fn, slot, a1 in [
      (hamilton_96_tiprack_10uL, 0, Coordinate(770.5, 146.0, 216.2)),
      (hamilton_96_tiprack_300uL, 4, Coordinate(770.5, 530.0, 216.2)),
    ]:
      with self.subTest(rack=rack_fn.__name__):
        deck = STARDeck()
        module = hamilton_mfx_tiprackholder_standard("module")
        deck.assign_child_resource(
          hamilton_mfx_carrier_L5_base("mfx_carrier", modules={slot: module}), track=30
        )
        module.assign_child_resource(rack := rack_fn("rack"))
        for spot, expected in (("A1", a1), ("H12", a1 + Coordinate(99.0, -63.0, 0.0))):
          actual = rack.get_item(spot).get_absolute_location("c", "c", "b")
          for axis in ("x", "y", "z"):
            self.assertAlmostEqual(getattr(actual, axis), getattr(expected, axis))

  def test_ntr_module_centres_its_rack_over_the_carrier_slot(self):
    """The module is 134 mm across in a 135 mm slot, which centres it, so it is modelled as the
    slot: a rack centred on the module is then centred on the slot."""
    deck = STARDeck()
    module = hamilton_mfx_module_tiprackholder_ntr("module")
    deck.assign_child_resource(
      hamilton_mfx_carrier_L5_base("mfx_carrier", modules={0: module}), track=30
    )
    module.assign_child_resource(rack := hamilton_96_tiprack_50uL_NTR("rack"))
    slot = module.get_absolute_location()
    self.assertAlmostEqual(rack.get_absolute_location().x - slot.x, (135.0 - rack.get_size_x()) / 2)
    self.assertAlmostEqual(rack.get_absolute_location().y - slot.y, (94.0 - rack.get_size_y()) / 2)
    self.assertAlmostEqual(rack.get_absolute_location().z - slot.z, 10.8)

  def test_raised_tip_rack_positions_on_star_deck(self):
    for rack_fn, a1_z in [
      (hamilton_96_tiprack_raised_core_ii, 230.95),
      (hamilton_96_tiprack_raised_core_i, 227.45),
    ]:
      with self.subTest(rack=rack_fn.__name__):
        deck = STARDeck()
        carrier = TIP_CAR_480BC_A00("carrier")
        carrier[0] = rack = rack_fn("rack", make_tip=hamilton_tip_10uL)
        deck.assign_child_resource(carrier, track=22)
        for spot, expected in (
          ("A1", Coordinate(590.4, 145.8, a1_z)),
          ("H12", Coordinate(689.4, 82.8, a1_z)),
        ):
          actual = rack.get_item(spot).get_absolute_location("c", "c", "b")
          for axis in ("x", "y", "z"):
            self.assertAlmostEqual(getattr(actual, axis), getattr(expected, axis))

  def test_non_embedded_racks_on_their_carriers(self):
    # TODO: model the 4 mL / 5 mL racks and the NTR, then place them like the standard rack
    for carrier_fn, rack_fn in [
      (TIP_CAR_72_4mlTF_C00, hamilton_24_tiprack_4000uL_filter),
      (TIP_CAR_96BC_5mlT_A00, hamilton_24_tiprack_5000uL),
      (TIP_CAR_NTR_A00, hamilton_96_tiprack_50uL_NTR),
    ]:
      with self.subTest(carrier=carrier_fn.__name__):
        carrier = carrier_fn("carrier")
        carrier[0] = rack = rack_fn("rack", with_tips=False)
        self.assertEqual(rack.location, Coordinate.zero())
