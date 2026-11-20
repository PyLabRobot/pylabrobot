"""A simulated instrument as `BiomekI7(simulation=True)` builds it: the Span-8's tip sensor reports
the tips the session records, a pod moves aside for the other, moves take the instrument's time
when asked to, and setup takes off tips left on (the 96-head, which cannot sense them, ejects over
its trash even with none recorded)."""

import asyncio
import os
import tempfile
import time
import unittest
from typing import Dict, List

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
    # Tips left on last session, as its saved state has them on the shafts: a Span-8 tip the sensor
    # finds, and the 96-head's, which it cannot sense, go to their trash; the state then has none.
    from pylabrobot.resources.n_channel_pipettes import NChannelPipette

    with tempfile.TemporaryDirectory() as d:
      path = os.path.join(d, "state.json")
      last = generic_biomek(state_json=path)
      left = last.pod_models["left"]
      head = left.get_resource(f"{left.name}_head")
      assert isinstance(head, NChannelPipette)
      for shaft in head.get_all_items():
        shaft.mount_tip(biomek_tip("T230", f"{shaft.name}_tip"))
      probe_3 = shaft_of(span8_channels(last.bridges["right"])[2])
      probe_3.mount_tip(biomek_tip("T230", "probe_tip"))
      last.save_state_to_file(path)

      biomek = generic_biomek(state_json=path)
      await biomek.setup()
      self.addAsyncCleanup(biomek.stop)
      assert biomek.pipettes is not None and biomek.head96 is not None
      self.assertEqual(biomek.pipettes.tips, [None] * 8)
      self.assertFalse(any(biomek.head96.tips))

      after = generic_biomek(state_json=path)
      left = after.pod_models["left"]
      head = left.get_resource(f"{left.name}_head")
      assert isinstance(head, NChannelPipette)
      self.assertFalse(any(shaft.has_tip() for shaft in head.get_all_items()))
      self.assertFalse(shaft_of(span8_channels(after.bridges["right"])[2]).has_tip())

  async def test_a_heads_tips_are_kept_across_sessions(self) -> None:
    # The 96-head cannot sense tips: those it picked up come back from the saved state.
    with tempfile.TemporaryDirectory() as d:
      path = os.path.join(d, "state.json")
      first = generic_biomek(state_json=path)
      await first.setup(precautionary_discard=False)
      assert first.head96 is not None
      first.head96._set_tips({"A1": biomek_tip("T230", "a1"), "B2": biomek_tip("T1025F", "b2")})
      await first.stop()

      second = generic_biomek(state_json=path)
      await second.setup(discard_tips=False)
      self.addAsyncCleanup(second.stop)
      assert second.head96 is not None
      tips = second.head96.tips
      self.assertEqual(tips[0], "beckman_coulter_biomek_tip_230uL")
      self.assertEqual(tips[13], "beckman_coulter_biomek_tip_1025uL_filter")
      self.assertEqual(sum(t is not None for t in tips), 2)


class PrecautionaryDiscardTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def setup_recording(self, **setup_kwargs):  # type: ignore[no-untyped-def]
    biomek = generic_biomek()
    moves: List[Dict[str, int]] = []
    biomek_setup = biomek.setup

    async def setup() -> None:
      await biomek_setup(discard_tips=False, park=False)
      pod = biomek.left_x_arm
      assert pod is not None
      original = pod.move_axes

      async def move_axes(targets, speed=1000, **kwargs):  # type: ignore[no-untyped-def]
        moves.append(dict(targets))
        return await original(targets, speed, **kwargs)

      pod.move_axes = move_axes  # type: ignore[method-assign, assignment]
      await biomek._discard_found_tips(set(), setup_kwargs.get("precautionary_discard", True))

    await setup()
    self.addAsyncCleanup(biomek.stop)
    return biomek, moves

  async def test_no_recorded_tips_still_ejects_over_the_trash(self) -> None:
    # The head cannot sense tips: with none recorded it ejects as if the longest were on, which
    # lands where Biomek's logged discards of 1025 uL tips (the same length) do.
    biomek, moves = await self.setup_recording()
    head = biomek.head96
    assert head is not None and biomek.left_x_arm is not None
    d = biomek.left_x_arm.declared
    assert d is not None
    plan = head.plan_discard(head._effective_tip_length(biomek_tip("T1025F", "t")))
    self.assertIn({"Z": d.to_counts("Z", plan.z)}, moves)
    self.assertTrue(any(m.get("D") == d.to_counts("D", d.limits["D"][0]) for m in moves))
    travel = next(m for m in moves if "X" in m and "Y" in m)
    self.assertEqual(
      (travel["X"], travel["Y"]), (d.to_counts("X", plan.x), d.to_counts("Y", plan.y))
    )
    self.assertFalse(any(head.tips))

  async def test_the_operator_can_skip_it(self) -> None:
    biomek, moves = await self.setup_recording(precautionary_discard=False)
    self.assertEqual(moves, [])


if __name__ == "__main__":
  unittest.main()
