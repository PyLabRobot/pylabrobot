"""Beckman Coulter Biomek tips, tip boxes, plates and reservoirs, against the figures of the Biomek
classes they transcribe (cm there, mm here)."""

import math
import unittest

from pylabrobot.resources import (
  BiomekTip,
  Plate,
  BiomekWell,
  Resource,
  WellSegment,
  beckman_coulter_biomek_24_wellplate_square_rb,
  beckman_coulter_biomek_96_deepwell_square,
  beckman_coulter_biomek_96_tiprack_50uL_filter,
  beckman_coulter_biomek_96_tiprack_190uL_filter,
  beckman_coulter_biomek_96_tiprack_230uL,
  beckman_coulter_biomek_96_tiprack_1025uL_filter,
  beckman_coulter_biomek_96_tiprack_1070uL,
  beckman_coulter_biomek_96_wellplate_flat,
  beckman_coulter_biomek_96_wellplate_vb,
  beckman_coulter_biomek_modular_reservoir,
  beckman_coulter_biomek_reservoir,
  beckman_coulter_biomek_reservoir_350cb,
  beckman_coulter_biomek_tip_50uL_filter,
  beckman_coulter_biomek_tip_190uL_filter,
  beckman_coulter_biomek_tip_230uL,
  beckman_coulter_biomek_tip_1025uL_filter,
  beckman_coulter_biomek_tip_1070uL,
)
from pylabrobot.resources.tip_rack import StandingTipRack


class BiomekTipTests(unittest.TestCase):
  def test_a_tip_has_its_class_figures(self) -> None:
    # tipheight, capacity, filtered, coniclength (cm); every class seats 0.775 cm on the mandrel.
    for make, length, volume, has_filter, cone in (
      (beckman_coulter_biomek_tip_50uL_filter, 51.359, 50.0, True, 38.227),
      (beckman_coulter_biomek_tip_190uL_filter, 51.435, 190.0, True, 28.194),
      (beckman_coulter_biomek_tip_230uL, 51.435, 230.0, False, 28.194),
      (beckman_coulter_biomek_tip_1025uL_filter, 109.855, 1025.0, True, 19.8374),
      (beckman_coulter_biomek_tip_1070uL, 109.855, 1070.0, False, 19.8374),
    ):
      with self.subTest(make.__name__):
        tip = make("tip")
        self.assertIsInstance(tip, BiomekTip)
        self.assertAlmostEqual(tip.get_size_z(), length)
        self.assertEqual(tip.maximal_volume, volume)
        self.assertEqual(tip.has_filter, has_filter)
        self.assertAlmostEqual(tip.fitting_depth, 7.75)
        self.assertAlmostEqual(tip.cone_length, cone)
        self.assertFalse(tip.is_conductive)
        self.assertEqual(tip.model, make.__name__)

  def test_a_tip_widens_along_its_cone(self) -> None:
    tip = beckman_coulter_biomek_tip_1070uL("tip")  # mintipradius 0.0635, maxtipradius 0.2413 cm
    self.assertAlmostEqual(tip.radius_at(0), 0.635)
    self.assertAlmostEqual(tip.radius_at(19.8374 / 2), (0.635 + 2.413) / 2)
    self.assertAlmostEqual(tip.radius_at(50), 2.413)

  def test_a_tip_reads_back_from_its_serialization(self) -> None:
    tip = beckman_coulter_biomek_tip_230uL("tip")
    back = Resource.deserialize(tip.serialize())
    self.assertIsInstance(back, BiomekTip)
    self.assertEqual(back, tip)
    self.assertNotEqual(back, beckman_coulter_biomek_tip_1070uL("tip"))


class BiomekTipBoxTests(unittest.TestCase):
  BOXES = (
    # make, height, well1xoffset (mm), tip length
    (beckman_coulter_biomek_96_tiprack_50uL_filter, 55.9, 14.5, 51.359),
    (beckman_coulter_biomek_96_tiprack_190uL_filter, 55.9, 14.5, 51.435),
    (beckman_coulter_biomek_96_tiprack_230uL, 55.9, 14.5, 51.435),
    (beckman_coulter_biomek_96_tiprack_1025uL_filter, 114.1, 14.4, 109.855),
    (beckman_coulter_biomek_96_tiprack_1070uL, 114.1, 14.4, 109.855),
  )

  def test_a_box_holds_96_of_its_tips_on_a_9_mm_grid(self) -> None:
    for make, _, _, tip_length in self.BOXES:
      with self.subTest(make.__name__):
        rack = make("rack")
        self.assertIsInstance(rack, StandingTipRack)
        self.assertEqual((rack.num_items_x, rack.num_items_y), (12, 8))
        self.assertTrue(all(spot.has_tip() for spot in rack.get_all_items()))
        self.assertAlmostEqual(rack.get_item("A1").get_tip().get_size_z(), tip_length)
        a1 = rack.get_item("A1").get_location_wrt(rack)
        a2 = rack.get_item("A2").get_location_wrt(rack)
        b1 = rack.get_item("B1").get_location_wrt(rack)
        self.assertAlmostEqual(a2.x - a1.x, 9.0)
        self.assertAlmostEqual(a1.y - b1.y, 9.0)

  def test_tip_a1_stands_where_the_class_puts_it(self) -> None:
    """A1's centre is `well1xoffset` from the left edge and 1.12 cm from the back edge; the tips'
    tops are at the box's height, where the head's mandrels meet them."""
    for make, height, a1_x, _ in self.BOXES:
      with self.subTest(make.__name__):
        rack = make("rack")
        self.assertEqual((rack.get_size_x(), rack.get_size_y()), (127.9, 85.4))
        self.assertAlmostEqual(rack.get_size_z(), height)
        centre = rack.get_item("A1").get_location_wrt(rack, "c", "c", "b")
        self.assertAlmostEqual(centre.x, a1_x)
        self.assertAlmostEqual(centre.y, 85.4 - 11.2)
        self.assertAlmostEqual(centre.z, height)

  def test_a_box_is_gripped_below_its_top(self) -> None:
    # gripperinfo: the pads at its centre, the fingertips 0.23 cm below its bottom.
    rack = beckman_coulter_biomek_96_tiprack_230uL("rack")
    point = rack.preferred_pickup_location
    assert point is not None
    self.assertAlmostEqual(point.x, 127.9 / 2)
    self.assertAlmostEqual(point.y, 85.4 / 2)
    self.assertAlmostEqual(point.z, -2.3)


class BiomekPlateTests(unittest.TestCase):
  def test_a_segment_holds_biomeks_section_volume(self) -> None:
    # `sectionvolume` (uL) of each segment, from its outline: a cone, a frustum, a rectangular
    # section, a round bottom (a spherical cap).
    for make, volumes in (
      (beckman_coulter_biomek_96_wellplate_vb, (15.6665, 427.2566)),
      (beckman_coulter_biomek_96_wellplate_flat, (365.708,)),
      (beckman_coulter_biomek_96_deepwell_square, (133.645, 2282.995)),
      (beckman_coulter_biomek_24_wellplate_square_rb, (1072.3303, 9248.0)),
    ):
      well = make("plate").get_well("A1")
      assert isinstance(well, BiomekWell)
      for segment, volume in zip(well.segments, volumes):
        with self.subTest(make.__name__, volume=volume):
          self.assertAlmostEqual(segment.volume(segment.height), volume, delta=0.002 * volume)

  def test_the_logged_liquid_level(self) -> None:
    # 200 uL in Thermo96VB_PP: the 96-head's logged dispense rose 0.567 cm with the liquid.
    well = beckman_coulter_biomek_96_wellplate_vb("plate").get_well("A1")
    self.assertAlmostEqual(well.compute_height_from_volume(200), 5.667, delta=0.01)
    assert isinstance(well, BiomekWell)
    self.assertAlmostEqual(well.wall_distance(3.67, 0.0), 4.0)

  def test_a_square_well_meets_its_wall_at_the_side(self) -> None:
    well = beckman_coulter_biomek_96_deepwell_square("plate").get_well("A1")
    assert isinstance(well, BiomekWell)
    self.assertAlmostEqual(well.get_size_x(), 8.28)
    self.assertAlmostEqual(well.wall_distance(20.0, 0.0), 4.14)
    self.assertAlmostEqual(well.wall_distance(20.0, math.pi / 4), 4.14 * math.sqrt(2))

  def test_a_well_reads_back_from_its_serialization(self) -> None:
    plate = beckman_coulter_biomek_96_wellplate_vb("plate", with_lid=True)
    back = Resource.deserialize(plate.serialize())
    assert isinstance(back, Plate)
    well = back.get_item("A1")
    assert isinstance(well, BiomekWell)
    self.assertEqual(well, plate.get_item("A1"))
    self.assertEqual(well.segments[0], WellSegment(2.0, 2.735, 2.735, 0.0, 0.0))

  def test_a_lid_sits_where_biomek_seats_it(self) -> None:
    # Logged: the fingertips 1.000 cm above the plate's bottom for TiterplateLid on Thermo96VB_PP,
    # and 4.200 cm for the Thomson lid on its plate. The lid covers the plate's footprint.
    for make, tips_above in (
      (beckman_coulter_biomek_96_wellplate_vb, 10.0),
      (beckman_coulter_biomek_24_wellplate_square_rb, 42.0),
    ):
      with self.subTest(make.__name__):
        plate = make("plate", with_lid=True)
        lid = plate.lid
        assert lid is not None and lid.location is not None
        point = lid.preferred_pickup_location
        assert point is not None
        self.assertAlmostEqual(lid.location.z + point.z, tips_above)
        self.assertEqual(
          (lid.get_size_x(), lid.get_size_y()), (plate.get_size_x(), plate.get_size_y())
        )

  def test_an_uncentred_seat_moves_the_grip_point(self) -> None:
    # The Thomson lid's seat is its plate's corner: its centre 0.381 mm left, 1.46 mm back.
    plate = beckman_coulter_biomek_24_wellplate_square_rb("plate", with_lid=True)
    lid = plate.lid
    assert lid is not None and lid.preferred_pickup_location is not None
    self.assertAlmostEqual(lid.preferred_pickup_location.x - lid.get_size_x() / 2, -0.381)
    self.assertAlmostEqual(lid.preferred_pickup_location.y - lid.get_size_y() / 2, 1.4605)

  def test_plates_are_gripped_below_their_bottom(self) -> None:
    plate = beckman_coulter_biomek_96_wellplate_vb("plate")
    assert plate.preferred_pickup_location is not None
    self.assertAlmostEqual(plate.preferred_pickup_location.z, -2.3)


class BiomekReservoirTests(unittest.TestCase):
  def test_a_reservoir_section_is_a_box(self) -> None:
    well = beckman_coulter_biomek_reservoir_350cb("res").get_well("A1")
    self.assertAlmostEqual(well.compute_volume_from_height(10), 123.8 * 81.5 * 10, delta=0.01)
    self.assertEqual(well.max_volume, 300000.0)

  def test_reservoirs_have_a_well_per_section(self) -> None:
    self.assertEqual(len(beckman_coulter_biomek_reservoir("res").children), 1)
    modular = beckman_coulter_biomek_modular_reservoir("mod")
    self.assertEqual(len(modular.children), 4)
    a1 = modular.get_item("A1")
    self.assertAlmostEqual(a1.get_size_x(), 23.65)  # x1 1.105 to x2 3.47 cm
    assert isinstance(a1, BiomekWell)
    self.assertEqual(a1.segments, ())  # a V bottom Biomek does not outline


if __name__ == "__main__":
  unittest.main()
