import io
import unittest
from contextlib import redirect_stdout

from pylabrobot.resources.tecan.tip_creators import DiTi_10ul_LiHa_tip


class TecanTipCreatorTests(unittest.TestCase):
  """Tests for Tecan tip factories."""

  def test_invalid_total_tip_length_uses_warning_not_stdout(self):
    stdout = io.StringIO()

    with redirect_stdout(stdout):
      with self.assertWarnsRegex(UserWarning, "total_tip_length <= 0"):
        DiTi_10ul_LiHa_tip(name="tip")

    self.assertNotIn("total_tip_length <= 0", stdout.getvalue())


if __name__ == "__main__":
  unittest.main()
