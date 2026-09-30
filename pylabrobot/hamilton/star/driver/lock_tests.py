import asyncio
import unittest
from typing import List

from pylabrobot.hamilton.star.driver.lock import _FirmwareLock


class TestChannelLocks(unittest.IsolatedAsyncioTestCase):
  """Each channel has its own lock; a C0 command driving the channels takes them all."""

  async def run_together(self, first: str, second: str) -> List[str]:
    """Hold `first`, start `second`, and return the order the two entered and left."""
    lock = _FirmwareLock()
    events: List[str] = []
    entered = asyncio.Event()

    async def hold(key: str):
      async with lock.subsystem(key):
        events.append(f"in {key}")
        entered.set()
        await asyncio.sleep(0.01)
        events.append(f"out {key}")

    async def follow(key: str):
      await entered.wait()
      async with lock.subsystem(key):
        events.append(f"in {key}")
        events.append(f"out {key}")

    await asyncio.gather(hold(first), follow(second))
    return events

  async def test_two_channels_overlap(self):
    self.assertEqual(await self.run_together("P1", "P2"), ["in P1", "in P2", "out P2", "out P1"])

  async def test_one_channel_does_not(self):
    self.assertEqual(await self.run_together("P1", "P1"), ["in P1", "out P1", "in P1", "out P1"])

  async def test_a_channels_command_and_a_channel_wait_for_each_other(self):
    channels = _FirmwareLock.CHANNELS
    self.assertEqual(
      await self.run_together("P5", channels),
      ["in P5", "out P5", f"in {channels}", f"out {channels}"],
    )
    self.assertEqual(
      await self.run_together(channels, "P5"),
      [f"in {channels}", f"out {channels}", "in P5", "out P5"],
    )
