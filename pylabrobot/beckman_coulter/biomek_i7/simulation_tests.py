"""A simulated instrument as `BiomekI7(simulation=True)` builds it: a pod moves aside for the other,
and moves take the instrument's time when asked to."""

import asyncio
import time
import unittest

from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_biomek


class SimulationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Debug mode makes a simulated Home All ~100x slower; see driver/homing_tests.py.
    asyncio.get_running_loop().set_debug(False)
    self.biomek = generic_biomek()
    await self.biomek.setup()
    self.addAsyncCleanup(self.biomek.stop)

  async def test_a_pod_moves_aside(self) -> None:
    pod = self.biomek.left_x_arm
    assert pod is not None and pod.declared is not None
    await pod.move_aside()
    low, _ = pod.declared.limits["X"]
    self.assertEqual(pod.cpositions["X"][1], pod.declared.to_counts("X", low))


class TimedSimulationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def test_moves_take_the_instruments_time(self) -> None:
    # A long X move takes over a second on the instrument, and a quarter of that simulated by
    # default (`motion_time_scale`); untimed, it answers at once.
    took = {}
    for timed in (False, True):
      biomek = generic_biomek(simulate_motion_time=timed)
      await biomek.setup(park=False)
      self.addAsyncCleanup(biomek.stop)
      pod = biomek.left_x_arm
      assert pod is not None and pod.declared is not None
      started = time.monotonic()
      await pod.move_axes({"X": pod.declared.to_counts("X", 60.0)})
      took[timed] = time.monotonic() - started
    self.assertLess(took[False], 0.1)
    self.assertGreater(took[True], 0.2)


if __name__ == "__main__":
  unittest.main()
