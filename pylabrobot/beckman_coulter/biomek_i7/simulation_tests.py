"""A simulated instrument as `BiomekI7(simulation=True)` builds it: the Span-8's tip sensor reports
the tips the session records, a pod moves aside for the other, moves take the instrument's time
when asked to, and setup takes off tips left on."""

import asyncio
import os
import tempfile
import time
import unittest

from pylabrobot.beckman_coulter.biomek_i7.resource_model.heads import shaft_of
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_tip
from pylabrobot.beckman_coulter.biomek_i7.resource_model.pods import span8_channels
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_biomek


class SimulationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Debug mode makes a simulated Home All ~100x slower; see driver/homing_tests.py.
    asyncio.get_running_loop().set_debug(False)
    self.biomek = generic_biomek()
    await self.biomek.setup()
    self.addAsyncCleanup(self.biomek.stop)

  async def test_the_tip_sensor_reports_the_recorded_tips(self) -> None:
    pipettes = self.biomek.pipettes
    assert pipettes is not None
    self.assertEqual(await pipettes.sense_tip_presence(), ["unloaded"] * 8)
    pipettes._set_tips({2: biomek_tip("T230", "t")})
    self.assertEqual((await pipettes.sense_tip_presence())[:3], ["unloaded", "loaded", "unloaded"])

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


class SetupDiscardTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def test_setup_takes_off_tips_left_on(self) -> None:
    # A Span-8 tip left on last session, as its saved state has it on the shaft, and the sensor
    # finds: it goes to its trash, and the state then has none.
    with tempfile.TemporaryDirectory() as d:
      path = os.path.join(d, "state.json")
      last = generic_biomek(state_json=path)
      probe_3 = shaft_of(span8_channels(last.bridges["right"])[2])
      probe_3.mount_tip(biomek_tip("T230", "probe_tip"))
      last.save_state_to_file(path)

      biomek = generic_biomek(state_json=path)
      await biomek.setup()
      self.addAsyncCleanup(biomek.stop)
      assert biomek.pipettes is not None
      self.assertEqual(biomek.pipettes.tips, [None] * 8)
      after = generic_biomek(state_json=path)
      self.assertFalse(shaft_of(span8_channels(after.bridges["right"])[2]).has_tip())


if __name__ == "__main__":
  unittest.main()
