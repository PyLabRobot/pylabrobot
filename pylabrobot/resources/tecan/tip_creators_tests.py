import unittest

from pylabrobot.resources.tecan.tip_creators import DiTi_10ul_LiHa_tip
from pylabrobot.resources.tecan.tip_racks import Adapter_96_DiTi_MCA384


class TecanTipCreatorTests(unittest.TestCase):
  """Tests for Tecan tip factories."""

  def test_unknown_diameter_raises(self):
    """Incomplete catalog definitions must not substitute an arbitrary diameter."""
    for factory in (DiTi_10ul_LiHa_tip, Adapter_96_DiTi_MCA384):
      with self.subTest(factory=factory.__name__):
        with self.assertRaisesRegex(NotImplementedError, "Tip diameter is not defined"):
          factory(name="resource")


if __name__ == "__main__":
  unittest.main()
