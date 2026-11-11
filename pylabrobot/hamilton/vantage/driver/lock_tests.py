import asyncio
import unittest
from typing import List

from pylabrobot.hamilton.vantage.driver.lock import _FirmwareLock


class TestOneCommandPerModule(unittest.IsolatedAsyncioTestCase):
  """A module runs one command at a time. Different modules run together."""

  async def run_together(self, first: str, second: str) -> List[str]:
    """Hold `first`, start `second`, and return the order the two entered and left."""
    lock = _FirmwareLock()
    events: List[str] = []
    entered = asyncio.Event()

    async def hold(module: str):
      async with lock.module(module):
        events.append(f"in {module}")
        entered.set()
        await asyncio.sleep(0.01)
        events.append(f"out {module}")

    async def follow(module: str):
      await entered.wait()
      async with lock.module(module):
        events.append(f"in {module}")
        events.append(f"out {module}")

    await asyncio.gather(hold(first), follow(second))
    return events

  async def test_two_modules_overlap(self):
    self.assertEqual(
      await self.run_together("A1AM", "A1PM"), ["in A1AM", "in A1PM", "out A1PM", "out A1AM"]
    )

  async def test_one_module_does_not(self):
    self.assertEqual(
      await self.run_together("A1PM", "A1PM"), ["in A1PM", "out A1PM", "in A1PM", "out A1PM"]
    )

  async def test_commands_to_a_module_run_in_the_order_they_came(self):
    lock = _FirmwareLock()
    order: List[int] = []

    async def command(number: int):
      async with lock.module("A1PM"):
        order.append(number)
        await asyncio.sleep(0)

    await asyncio.gather(*(command(number) for number in range(5)))
    self.assertEqual(order, [0, 1, 2, 3, 4])

  async def test_a_command_that_raises_lets_the_next_one_run(self):
    lock = _FirmwareLock()
    with self.assertRaises(RuntimeError):
      async with lock.module("A1PM"):
        raise RuntimeError("the command failed")
    await asyncio.wait_for(self._enter(lock), 1)

  async def test_a_command_that_is_cancelled_while_waiting_does_not_hold_the_module(self):
    lock = _FirmwareLock()
    holding = asyncio.Event()
    release = asyncio.Event()

    async def hold():
      async with lock.module("A1PM"):
        holding.set()
        await release.wait()

    async def wait_for_it():
      async with lock.module("A1PM"):
        pass

    holder = asyncio.ensure_future(hold())
    await holding.wait()
    waiter = asyncio.ensure_future(wait_for_it())
    await asyncio.sleep(0.01)
    waiter.cancel()
    release.set()
    await holder
    with self.assertRaises(asyncio.CancelledError):
      await waiter
    await asyncio.wait_for(self._enter(lock), 1)

  async def _enter(self, lock: _FirmwareLock) -> None:
    async with lock.module("A1PM"):
      pass


if __name__ == "__main__":
  unittest.main()
