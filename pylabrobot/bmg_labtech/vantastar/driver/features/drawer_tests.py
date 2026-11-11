"""Drawer tests over the driver's fake USB link, with status replies recorded from the reader."""

import unittest
from collections import deque

from pylabrobot.bmg_labtech.vantastar.driver.master_tests import (
  PLATE_IN,
  PLATE_OUT,
  STATUS_DRAWER_OPEN,
  STATUS_IDLE,
  STATUS_MOTORS_OFF,
  commands,
  make_driver,
)


class DrawerTests(unittest.IsolatedAsyncioTestCase):
  async def test_open_and_close_send_the_moves_and_update_drawer_open(self):
    """Opening and closing send Voyager's plate out and plate in commands."""
    replies = deque(
      [STATUS_IDLE, STATUS_IDLE, STATUS_DRAWER_OPEN, STATUS_DRAWER_OPEN, STATUS_IDLE, STATUS_IDLE]
    )
    driver = make_driver(lambda p: replies.popleft())
    self.assertIsNone(driver.drawer.drawer_open)
    await driver.drawer.open()
    self.assertTrue(driver.drawer.drawer_open)
    await driver.drawer.close()
    self.assertFalse(driver.drawer.drawer_open)
    self.assertEqual([c for c in commands(driver) if c[0] == 0x03], [PLATE_OUT, PLATE_IN])

  async def test_a_move_the_driver_makes_updates_drawer_open(self):
    """A shake that moves the plate in leaves ``drawer_open`` current."""
    replies = deque([STATUS_MOTORS_OFF] + [STATUS_IDLE] * 6)
    driver = make_driver(lambda p: replies.popleft())
    driver.drawer.drawer_open = True
    await driver.shaker.shake("double_orbital", 300, 10)
    self.assertFalse(driver.drawer.drawer_open)
