import unittest

from pylabrobot.lib.absorbance.results import get_grid
from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb


class GetGridTests(unittest.TestCase):
  def test_values_land_on_their_wells(self):
    plate = biorad_96_wellplate_200uL_Vb("plate")
    grid = get_grid(plate, {"A1": 0.1, "B3": 0.2, "H12": 0.3})
    self.assertEqual((len(grid), len(grid[0])), (8, 12))
    self.assertEqual((grid[0][0], grid[1][2], grid[7][11]), (0.1, 0.2, 0.3))
    self.assertEqual(sum(value is not None for row in grid for value in row), 3)

  def test_a_well_the_plate_lacks_is_refused(self):
    with self.assertRaises(ValueError):
      get_grid(biorad_96_wellplate_200uL_Vb("plate"), {"I1": 0.1})


if __name__ == "__main__":
  unittest.main()
