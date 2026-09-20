import io
import unittest
from contextlib import redirect_stdout

from pylabrobot.resources.tecan.tip_creators import DiTi_10ul_LiHa_tip


class TecanTipCreatorTests(unittest.TestCase):
  """Tests for Tecan tip factories."""

  def test_invalid_size_z_uses_warning_not_stdout(self):
    stdout = io.StringIO()

    with redirect_stdout(stdout):
      with self.assertWarnsRegex(UserWarning, "size_z <= 0"):
        DiTi_10ul_LiHa_tip(name="tip", diameter=6.0)

    self.assertNotIn("size_z <= 0", stdout.getvalue())


if __name__ == "__main__":
  unittest.main()
