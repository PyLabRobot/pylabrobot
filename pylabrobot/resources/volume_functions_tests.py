import math
import unittest

from pylabrobot.resources.volume_functions import (
  calculate_liquid_volume_container_2segments_square_vbottom,
  calculate_liquid_volume_container_2segments_round_vbottom,
  calculate_liquid_volume_container_2segments_round_ubottom,
  calculate_liquid_volume_container_2segments_square_ubottom,
)


class TestVolumeFunctions(unittest.TestCase):
  """ Tests for the volume functions """
  def test_calculate_liquid_volume_container_2segments_square_vbottom(self):
    # Exactly the full pyramid
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_square_vbottom(
        x=10, y=10, h_pyramid=10, h_cube=10, liquid_height=10),
      (10*10*10)/3
    )
    # Test for liquid height within the pyramid

    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_square_vbottom(
        x=10, y=10, h_pyramid=10, h_cube=10, liquid_height=5),
      (10*10*10)/3 * (5/10)**3
    )

    # Test for liquid height extending into the cube
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_square_vbottom(
        x=10, y=10, h_pyramid=10, h_cube=10, liquid_height=15),
      (10*10*10)/3 + 10*10*5
    )

  def test_calculate_liquid_volume_container_2segments_round_vbottom(self):
    # Exactly the full cone
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_round_vbottom(
        d=10, h_cone=10, h_cylinder=10, liquid_height=10),
        math.pi * (10/2)**2 * 10 / 3)

    # Test for liquid height within the cone
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_round_vbottom(
        d=10, h_cone=10, h_cylinder=10, liquid_height=5),
        math.pi * (10/2)**2 * 10 / 3 * (5/10)**3)

    # Test for liquid height extending into the cylinder
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_round_vbottom(
        d=10, h_cone=10, h_cylinder=10, liquid_height=15),
        math.pi * (10/2)**2 * 10 / 3 + math.pi * (10/2)**2 * 5)

  def test_calculate_liquid_volume_container_2segments_round_ubottom(self):
    # Exactly half the sphere
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_round_ubottom(
        d=10, h_cylinder=10, liquid_height=5),
        (2/3) * math.pi * 5**3)

    # Test for liquid height within the half sphere
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_round_ubottom(
        d=10, h_cylinder=10, liquid_height=5),
        (1/3) * math.pi * 5**2 * (3*5 - 5))

    # Test for liquid height extending into the cylinder
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_round_ubottom(
        d=10, h_cylinder=10, liquid_height=15),
        (2/3) * math.pi * 5**3 + math.pi * (10/2)**2 * 10)

  def test_calculate_liquid_volume_container_2segments_square_ubottom(self):
    # Exactly the full hemisphere
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_square_ubottom(
        x=10, h_cuboid=10, liquid_height=5),
        (2/3) * math.pi * (10/2)**3)

    # Test for liquid height within the hemisphere
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_square_ubottom(
        x=10, h_cuboid=10, liquid_height=5),
        (1/3) * math.pi * 5**2 * (3*5 - 5))

    # Test for liquid height extending into the cuboid
    self.assertAlmostEqual(
      calculate_liquid_volume_container_2segments_square_ubottom(
        x=10, h_cuboid=10, liquid_height=15),
        (2/3) * math.pi * (10/2)**3 + 10**2 * 10)
