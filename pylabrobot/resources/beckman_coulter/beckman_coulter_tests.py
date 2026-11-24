"""Tests for Biomek tip behavior."""

import unittest

from pylabrobot.resources import BiomekTip, Resource


class BiomekTipTests(unittest.TestCase):
  def setUp(self) -> None:
    self.tip = BiomekTip(
      name="tip",
      diameter=4,
      size_z=50,
      has_filter=False,
      maximal_volume=1000,
      fitting_depth=5,
      end_diameter=1,
      cone_length=20,
    )

  def test_a_tip_widens_along_its_cone(self) -> None:
    self.assertAlmostEqual(self.tip.radius_at(0), 0.5)
    self.assertAlmostEqual(self.tip.radius_at(10), 1.25)
    self.assertAlmostEqual(self.tip.radius_at(50), 2)

  def test_a_tip_reads_back_from_its_serialization(self) -> None:
    back = Resource.deserialize(self.tip.serialize())
    self.assertIsInstance(back, BiomekTip)
    self.assertEqual(back, self.tip)
    assert isinstance(back, BiomekTip)
    back.end_diameter = 2
    self.assertNotEqual(back, self.tip)


if __name__ == "__main__":
  unittest.main()
