"""Shaker tests over the driver's fake USB link, with status replies recorded from the reader."""

import unittest
from collections import deque

from pylabrobot.bmg_labtech.vantastar.driver.features.shaker import Shaker
from pylabrobot.bmg_labtech.vantastar.driver.master_tests import (
  PLATE_IN,
  SHAKE_DOUBLE_ORBITAL_300_10,
  STATUS_IDLE,
  STATUS_MOTORS_OFF,
  commands,
  make_driver,
)


class ShakeCommandTests(unittest.TestCase):
  """The IdleMoveCommand a shake builds."""

  def test_shake_matches_voyager(self):
    """Double orbital shaking at 300 rpm for 10 s builds Voyager's command."""
    body = Shaker.build_shake("double_orbital", 300, 10)
    self.assertEqual(bytes([0x27]) + body, SHAKE_DOUBLE_ORBITAL_300_10)

  def test_long_shake_pauses(self):
    """A 600 s shake pauses 1 s every 59 s."""
    body = Shaker.build_shake("orbital", 500, 600)
    self.assertEqual(body.hex(), "05040258" + "0001" + "003b")

  def test_shake_speed_must_be_a_multiple_of_100(self):
    """A shake speed that is not a multiple of 100 rpm is refused."""
    with self.assertRaises(ValueError):
      Shaker.build_shake("orbital", 250, 10)


class ShakerTests(unittest.IsolatedAsyncioTestCase):
  """Shaking through the driver."""

  async def test_shake_moves_the_plate_in_when_the_motors_are_off(self):
    """Shaking with the motors off moves the plate in first, then shakes."""
    replies = deque([STATUS_MOTORS_OFF] + [STATUS_IDLE] * 6)
    driver = make_driver(lambda p: replies.popleft())
    await driver.shaker.shake("double_orbital", 300, 10)
    sent = [c for c in commands(driver) if c[0] != 0x80]
    self.assertEqual(sent, [PLATE_IN, SHAKE_DOUBLE_ORBITAL_300_10])


if __name__ == "__main__":
  unittest.main()
