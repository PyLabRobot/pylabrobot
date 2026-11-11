"""Board errors as the Master Controller words them in its replies, as its logs show them."""

import unittest

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import ace_error, english

NAMES = {(0x11, 1): "left X", (0x11, 3): "left Z"}


class EnglishTests(unittest.TestCase):
  def test_not_homed_and_interlock(self) -> None:
    self.assertEqual(
      english(ace_error(0x32, None, "Set X Move Parameters"), NAMES),
      "The instrument needs to be homed.",
    )
    self.assertEqual(
      english(ace_error(0x0F, None, "Move All"), NAMES),
      "A light curtain violation has occurred.",
    )

  def test_a_coordinated_move_names_each_axis_that_failed(self) -> None:
    error = ace_error(0x38, None, "Move All", 0x10, 0)
    error.axis_errors = [
      ace_error(0x3C, 0x0100, "device 0x11 index 1", 0x11, 1),
      ace_error(0x3C, None, "device 0x11 index 3", 0x11, 3),
    ]
    self.assertEqual(
      english(error, NAMES),
      "The left X motion failed: motion error window exceeded. "
      "The left Z motion failed: motion error window exceeded.",
    )

  def test_an_axis_error_and_anything_else(self) -> None:
    self.assertEqual(
      english(ace_error(0x31, None, "Move", 0x11, 1), NAMES),
      "The left X motion failed: motion device not enabled.",
    )
    self.assertEqual(
      english(ace_error(0x0A, None, "Get Tip Status"), NAMES),
      "Get Tip Status failed: ACE error 0x0A.",
    )
