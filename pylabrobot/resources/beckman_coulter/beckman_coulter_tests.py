"""Beckman Coulter Biomek resource behavior: tip cones, height↔volume from well sections, and
plain `Well` plate factories."""

import unittest

from pylabrobot.resources import (
  BiomekTip,
  Plate,
  Resource,
  Well,
  beckman_coulter_biomek_24_wellplate_square_rb,
  beckman_coulter_biomek_96_deepwell_square,
  beckman_coulter_biomek_96_wellplate_flat,
  beckman_coulter_biomek_96_wellplate_vb,
  beckman_coulter_biomek_modular_reservoir,
  beckman_coulter_biomek_reservoir_350cb,
  beckman_coulter_biomek_tip_230uL,
  beckman_coulter_biomek_tip_1070uL,
)
from pylabrobot.resources.beckman_coulter.biomek_resource import (
  _WellSegment,
  height_volume_data_from_segments,
)
from pylabrobot.resources.beckman_coulter.plates import (
  _24_WELLPLATE_SQUARE_RB_SEGMENTS,
  _96_DEEPWELL_SQUARE_SEGMENTS,
  _96_WELLPLATE_FLAT_SEGMENTS,
  _96_WELLPLATE_VB_SEGMENTS,
)


class BiomekTipTests(unittest.TestCase):
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


class HeightVolumeFromSegmentsTests(unittest.TestCase):
  def test_a_segment_holds_biomeks_section_volume(self) -> None:
    # `sectionvolume` (uL) of each segment, from its outline: a cone, a frustum, a rectangular
    # section, a round bottom (a spherical cap).
    for segments, volumes in (
      (_96_WELLPLATE_VB_SEGMENTS, (15.6665, 427.2566)),
      (_96_WELLPLATE_FLAT_SEGMENTS, (365.708,)),
      (_96_DEEPWELL_SQUARE_SEGMENTS, (133.645, 2282.995)),
      (_24_WELLPLATE_SQUARE_RB_SEGMENTS, (1072.3303, 9248.0)),
    ):
      for segment, volume in zip(segments, volumes):
        with self.subTest(volume=volume):
          self.assertAlmostEqual(segment.volume(segment.height), volume, delta=0.002 * volume)

  def test_the_table_is_monotonic_to_the_rim(self) -> None:
    segments = (
      _WellSegment(2.0, 2.735, 2.735, 0.0, 0.0),
      _WellSegment(8.5, 4.0, 4.0, 4.0, 4.0),
    )
    table = height_volume_data_from_segments(segments, 10.5)
    heights = sorted(table)
    self.assertEqual(heights[0], 0.0)
    self.assertEqual(heights[-1], 10.5)
    volumes = [table[h] for h in heights]
    self.assertEqual(volumes, sorted(volumes))
    self.assertGreater(volumes[-1], volumes[0])


class BiomekPlateTests(unittest.TestCase):
  def test_wells_are_plain_wells_with_volume_functions(self) -> None:
    for make in (
      beckman_coulter_biomek_96_wellplate_vb,
      beckman_coulter_biomek_96_wellplate_flat,
      beckman_coulter_biomek_96_deepwell_square,
      beckman_coulter_biomek_24_wellplate_square_rb,
    ):
      with self.subTest(make.__name__):
        well = make("plate").get_well("A1")
        self.assertIs(type(well), Well)
        self.assertTrue(well.supports_compute_height_volume_functions())

  def test_the_logged_liquid_level(self) -> None:
    # 200 uL in Thermo96VB_PP: the 96-head's logged dispense rose 0.567 cm with the liquid.
    well = beckman_coulter_biomek_96_wellplate_vb("plate").get_well("A1")
    self.assertAlmostEqual(well.compute_height_from_volume(200), 5.667, delta=0.01)

  def test_a_plate_reads_back_from_its_serialization(self) -> None:
    plate = beckman_coulter_biomek_96_wellplate_vb("plate", with_lid=True)
    back = Resource.deserialize(plate.serialize())
    assert isinstance(back, Plate)
    well = back.get_item("A1")
    self.assertIs(type(well), Well)
    self.assertEqual(well, plate.get_item("A1"))


class BiomekReservoirTests(unittest.TestCase):
  def test_a_reservoir_section_is_a_box(self) -> None:
    well = beckman_coulter_biomek_reservoir_350cb("res").get_well("A1")
    self.assertIs(type(well), Well)
    self.assertAlmostEqual(well.compute_volume_from_height(10), 123.8 * 81.5 * 10, delta=0.01)
    self.assertEqual(well.max_volume, 300000.0)

  def test_modular_sections_are_plain_wells(self) -> None:
    modular = beckman_coulter_biomek_modular_reservoir("mod")
    self.assertEqual(len(modular.children), 4)
    a1 = modular.get_item("A1")
    self.assertIs(type(a1), Well)
    self.assertAlmostEqual(a1.get_size_x(), 23.65)


if __name__ == "__main__":
  unittest.main()
