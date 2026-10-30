import unittest

from pylabrobot.hamilton.vantage.driver.configuration import (
  ARM_TYPES_WITH_IPG,
  has_ipg_node,
  ipg_is_fitted,
)


class TestIpgIsFitted(unittest.TestCase):
  """An IPG counts as fitted if the arm type or the node names say so."""

  def test_the_arm_types_that_name_an_ipg(self):
    self.assertEqual(ARM_TYPES_WITH_IPG, {1, 2, 3, 5, 6, 7, 8, 10})

  def test_an_arm_type_that_names_one_is_enough(self):
    for arm_type in ARM_TYPES_WITH_IPG:
      self.assertTrue(ipg_is_fitted(arm_type, ["AM", "PX"]), arm_type)

  def test_an_arm_type_that_names_none_is_not(self):
    for arm_type in (0, 4, 9, 11, 12, 13, 99):
      self.assertFalse(ipg_is_fitted(arm_type, ["AM", "PX"]), arm_type)

  def test_a_node_name_that_starts_with_r_is_enough(self):
    self.assertTrue(ipg_is_fitted(13, ["AM", "01", "R0", "PX"]))
    self.assertTrue(has_ipg_node(["R0"]))

  def test_nodes_without_one_are_not(self):
    self.assertFalse(has_ipg_node(["AM", "01", "PX", "H0", "D0", "16"]))
    self.assertFalse(has_ipg_node([]))

  def test_a_name_that_only_contains_r_is_not_the_ipg(self):
    self.assertFalse(has_ipg_node(["PR", "AR"]))

  def test_either_alone_is_enough_and_neither_is_none(self):
    self.assertTrue(ipg_is_fitted(6, []))
    self.assertTrue(ipg_is_fitted(13, ["R0"]))
    self.assertFalse(ipg_is_fitted(13, []))


if __name__ == "__main__":
  unittest.main()
