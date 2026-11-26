"""Faults the boards report unasked, as the Master Controller handles them, and the positions: a
motor that fails on its own trips the interlock and every position is forgotten; after an interlock
trip, re-arming reads every position again."""

import asyncio

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest


class FaultTests(PodMoveTest):
  async def settle(self) -> None:
    """Let the boards' read loops and the driver's background writes run."""
    for _ in range(5):
      await asyncio.sleep(0.02)

  def unasked(self, driver: BiomekI7Driver, module: str, message: bytes) -> None:
    self.usb(driver, module)._queue(message)

  async def test_a_motor_failing_on_its_own_trips_the_interlock(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    await pod.move_axes({"X": 100000})
    x = pod.axis("X")
    self.mark(driver)
    with self.assertLogs("pylabrobot.beckman_coulter.biomek_i7.driver.master", "ERROR") as logs:
      # A Move All's NOTICE for X with a following error, sequence 0: nobody asked.
      self.unasked(driver, "multichannel", bytes([x.device, x.index, 0, 0, 0x50, 0xFE, 0x3C]))
      await self.settle()
    self.assertIn("The left X motor failed", "\n".join(logs.output))
    self.assertEqual(
      [n for n, _ in self.sent(driver, "chassis")], ["Trip Interlock", "Set Status Display"]
    )
    self.assertEqual(self.sent(driver, "chassis")[1][1], p.set_status_display("error").params)
    self.assertIsNone(pod.cpositions["X"][0])
    self.assertTrue(driver.interlock_tripped.is_set())

  async def test_an_interlock_trip_forgets_positions_and_shows_error(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    await pod.move_axes({"Z1": -10000})
    self.mark(driver)
    with self.assertLogs("pylabrobot.beckman_coulter.biomek_i7.driver.master", "ERROR"):
      self.unasked(driver, "chassis", bytes([p.Device.INTERLOCK, 1, 0, 0, 0x10, 0x03, 0x00, 0x01]))
      await self.settle()
    self.assertEqual(
      self.sent(driver, "chassis"), [("Set Status Display", p.set_status_display("error").params)]
    )
    self.assertTrue(all(c[0] is None for c in pod.cpositions.values() if c is not None))
    # Re-arming reads every position again, as the Master Controller's resume does.
    await driver.arm_interlock()
    self.assertFalse(driver.interlock_tripped.is_set())
    self.assertIsNotNone(pod.cpositions["Z1"][0])


if __name__ == "__main__":
  import unittest

  unittest.main()
