import unittest

from pylabrobot.brooks.precise_flex import kinematics


class TestClassifyPF400Reach(unittest.TestCase):
  """Link lengths are classified as standard, extended, or unknown reach."""

  def test_classify_pf400_reach(self):
    self.assertEqual(kinematics._classify_pf400_reach((225, 210)), "standard")
    self.assertEqual(kinematics._classify_pf400_reach((302, 289)), "extended")
    self.assertEqual(kinematics._classify_pf400_reach((303, 288)), "extended")  # within tolerance
    self.assertEqual(kinematics._classify_pf400_reach((500, 500)), "unknown")
