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
  hamilton_96_tiprack_10uL_NTR,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL,
  hamilton_96_tiprack_300uL_NTR,
  hamilton_96_tiprack_1000uL,
  hamilton_96_tiprack_raised_core_i,
  hamilton_96_tiprack_raised_core_ii,
  hamilton_mfx_carrier_L5_base,
  hamilton_mfx_resourceholder_ntr,
  hamilton_mfx_tiprackholder_standard,
  hamilton_tip_10uL,
  hamilton_tip_carrier_L5_ntr_a00,
)
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.tip_rack import StandingTipRack


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
    # positions whose firmware commands match Hamilton's own software
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
    module = hamilton_mfx_resourceholder_ntr("module")
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
    # TODO: model the 4 mL / 5 mL racks, then place them like the standard rack
    for carrier_fn, rack_fn in [
      (TIP_CAR_72_4mlTF_C00, hamilton_24_tiprack_4000uL_filter),
      (TIP_CAR_96BC_5mlT_A00, hamilton_24_tiprack_5000uL),
    ]:
      with self.subTest(carrier=carrier_fn.__name__):
        carrier = carrier_fn("carrier")
        carrier[0] = rack = rack_fn("rack", with_tips=False)
        self.assertEqual(rack.location, Coordinate.zero())


class NestedTipCarrierTests(unittest.TestCase):
  def test_tip_spot_positions_on_star_deck(self):
    # Hamilton's pick-up positions, with the carrier at x 752.5
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

  def test_tip_spot_positions_on_the_mfx_ntr4_module(self):
    # Hamilton's pick-up positions, with the MFX carrier at x 932.5 and the module in slot 3: the
    # rack centred on the module, the module on the carrier's site.
    for rack_fn in (
      hamilton_96_tiprack_10uL_NTR,
      hamilton_96_tiprack_50uL_NTR,
      hamilton_96_tiprack_300uL_NTR,
    ):
      with self.subTest(rack=rack_fn.__name__):
        deck = STARDeck()
        module = hamilton_mfx_resourceholder_ntr("module")
        carrier = hamilton_mfx_carrier_L5_base("carrier", modules={3: module})
        module.assign_child_resource(rack := rack_fn("rack"))
        deck.assign_child_resource(carrier, location=Coordinate(932.5, 63, 100))
        for spot, expected in (
          ("A1", Coordinate(950.5, 434.0, 184.0)),
          ("H12", Coordinate(1049.5, 371.0, 184.0)),
        ):
          actual = rack.get_item(spot).get_absolute_location("c", "c", "b")
          for axis in ("x", "y", "z"):
            self.assertAlmostEqual(getattr(actual, axis), getattr(expected, axis))

  def test_standard_tiprack_sinks_into_the_mfx_tiprackholder_as_into_a_tip_carrier(self):
    """The rack's skirt drops into the module, so its spots stand where a tip carrier puts them."""
    from pylabrobot.resources.hamilton import (
      TIP_CAR_480_A00,
      hamilton_96_tiprack_1000uL_filter,
      hamilton_mfx_tiprackholder_standard,
    )

    deck = STARDeck()
    carrier = TIP_CAR_480_A00("tip_carrier")
    carrier[0] = on_carrier = hamilton_96_tiprack_1000uL_filter("on_carrier")
    deck.assign_child_resource(carrier, track=1)
    module = hamilton_mfx_tiprackholder_standard("module")
    mfx = hamilton_mfx_carrier_L5_base("mfx", modules={0: module})
    module.assign_child_resource(on_module := hamilton_96_tiprack_1000uL_filter("on_module"))
    deck.assign_child_resource(mfx, location=Coordinate(932.5, 63, 100))

    carrier_z = on_carrier.get_item("A1").get_location_wrt(deck).z
    module_z = on_module.get_item("A1").get_location_wrt(deck).z
    self.assertAlmostEqual(module_z, 100 + 18.2 + 96.5 - 6.0 + 7.5)
    self.assertLess(abs(module_z - carrier_z), 0.3)

    # Hamilton's pick-up positions for a framed rack on the tip module, from the `1_Tip` and `3_Tip`
    # sites of an MFX carrier its software defines: the rack centred on the module, in slot 0.
    for spot, expected in (("A1", (950.5, 146.0)), ("H12", (1049.5, 83.0))):
      actual = on_module.get_item(spot).get_absolute_location("c", "c", "b")
      self.assertAlmostEqual(actual.x, expected[0])
      self.assertAlmostEqual(actual.y, expected[1])

  def test_a_solid_coreii_rack_has_the_spots_of_a_framed_rack_at_its_top(self):
    from pylabrobot.resources.hamilton import (
      TIP_CAR_480_A00,
      hamilton_96_tiprack_300uL,
      hamilton_96_tiprack_raised_core_i,
      hamilton_96_tiprack_raised_core_ii,
      hamilton_tip_300uL,
    )

    carrier = TIP_CAR_480_A00("tip_carrier")
    carrier[0] = framed = hamilton_96_tiprack_300uL("framed")
    carrier[1] = solid = hamilton_96_tiprack_raised_core_ii("solid", make_tip=hamilton_tip_300uL)
    carrier[2] = corei = hamilton_96_tiprack_raised_core_i("corei", make_tip=hamilton_tip_300uL)
    # Hamilton's definitions: 16.0 and 12.5 mm above the site
    for rack, above in ((solid, 16.0), (corei, 12.5)):
      spot_z = rack.get_item("A1").get_absolute_location("c", "c", "b").z
      self.assertAlmostEqual(spot_z - 114.95, above)
    for spot in ("A1", "H12"):
      framed_spot = framed.get_item(spot).get_absolute_location("c", "c", "b")
      solid_spot = solid.get_item(spot).get_absolute_location("c", "c", "b")
      self.assertAlmostEqual(solid_spot.x, framed_spot.x)
      self.assertAlmostEqual(
        solid_spot.y - carrier.sites[1].location.y, framed_spot.y - carrier.sites[0].location.y
      )
      self.assertAlmostEqual(solid_spot.z, solid.get_absolute_location().z + 25.5)

  def test_a_stack_of_nested_tip_racks_on_both_holders(self):
    # Each rack in a nest stands its 16 mm stacking height above the one below, so the top rack's
    # A1 is at 184.0 + 16 per rack below it. Derived: no capture has picked up from a nest.
    module = hamilton_mfx_resourceholder_ntr("module")
    mfx = hamilton_mfx_carrier_L5_base("mfx", modules={3: module})
    ntr_carrier = hamilton_tip_carrier_L5_ntr_a00("ntr_carrier")
    for holder, carrier, location, racks, a1 in [
      (module, mfx, Coordinate(932.5, 63, 100), 4, Coordinate(950.5, 434.0, 184.0 + 3 * 16)),
      (
        ntr_carrier.sites[1],
        ntr_carrier,
        Coordinate(752.5, 63, 100),
        2,
        Coordinate(770.4, 241.8, 200.0),
      ),
    ]:
      with self.subTest(holder=holder.name, racks=racks):
        deck = STARDeck()
        stack = ResourceStack(f"stack_{racks}", direction="z")
        holder.assign_child_resource(stack)
        for i in range(racks):
          stack.assign_child_resource(hamilton_96_tiprack_50uL_NTR(f"{holder.name}_ntr{i}"))
        deck.assign_child_resource(carrier, location=location)
        self.assertAlmostEqual(stack.get_size_z(), 55 + (racks - 1) * 16)
        top = stack.get_top_item()
        assert isinstance(top, StandingTipRack)
        actual = top.get_item("A1").get_absolute_location("c", "c", "b")
        for axis in ("x", "y", "z"):
          self.assertAlmostEqual(getattr(actual, axis), getattr(a1, axis))

  def test_a_tip_ends_above_the_nested_racks_bottom(self):
    # How far a tip's end stands above the rack's bottom, by tip size
    for rack_fn, tip_end in [
      (hamilton_96_tiprack_10uL_NTR, 31.0),
      (hamilton_96_tiprack_50uL_NTR, 12.6),
      (hamilton_96_tiprack_300uL_NTR, 3.0),
    ]:
      with self.subTest(rack=rack_fn.__name__):
        rack = rack_fn("rack")
        tip = rack.get_item("A1").get_tip()
        self.assertAlmostEqual(tip.get_location_wrt(rack).z, tip_end, delta=0.15)

  def test_the_carrier_stands_29_mm_above_its_sites(self):
    carrier = hamilton_tip_carrier_L5_ntr_a00("carrier")
    self.assertEqual(carrier.get_size_z(), 58.0)
    for site in carrier.sites.values():
      self.assertEqual(carrier.get_size_z() - site.location.z, 29.0)

  def test_the_old_carrier_name_still_works(self):
    with self.assertWarns(DeprecationWarning):
      old = TIP_CAR_NTR_A00("carrier")
    self.assertEqual(old, hamilton_tip_carrier_L5_ntr_a00("carrier"))
