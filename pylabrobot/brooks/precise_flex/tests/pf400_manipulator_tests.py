import unittest

from pylabrobot.brooks.precise_flex.resource_model import pf400_manipulator


class TestTheColumn(unittest.TestCase):
  """The column is the one sold for the arm's Z travel."""

  def test_a_column_is_the_one_sold_nearest_the_z_travel(self):
    # The soft limits stand a little past the travel a column is sold for.
    for z_travel, height, model in (
      (400.0, 679.0, "400mm"),
      (401.5, 679.0, "400mm"),
      (750.0, 1029.0, "750mm"),
      (1161.5, 1439.0, "1160mm"),
    ):
      with self.subTest(z_travel=z_travel):
        column = pf400_manipulator.z_column("column", pf400_manipulator.z_column_height(z_travel))
        # The datasheet's robot height, from the base plate's bottom.
        self.assertEqual(pf400_manipulator.Z_COLUMN_LOCATION.z + column.get_size_z(), height)
        self.assertEqual(column.model, f"brooks_pf400_z_column_{model}")


class TestPF400Links(unittest.TestCase):
  """The two links, drawn as the reach their lengths are nearest."""

  def test_each_link_is_drawn_as_the_nearest_reach(self):
    for l1, l2, reach in (
      (302.0, 289.0, "extended"),
      (225.0, 210.0, "standard"),
      (250.0, 240.0, "standard"),
    ):
      with self.subTest(l1=l1, l2=l2):
        first = pf400_manipulator.link_1("link_1", l1)
        second = pf400_manipulator.link_2("link_2", l2)
        models = (f"brooks_pf400_link_1_{reach}", f"brooks_pf400_link_2_{reach}")
        self.assertEqual((first.model, second.model), models)
