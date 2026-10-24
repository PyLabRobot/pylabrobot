import asyncio
import unittest
from datetime import timedelta
from typing import Any, Dict, List

from pylabrobot.utils.timing import get_current_cycle, run_cycles

NO_CYCLE = {"run_start_timestamp": None, "cycle": None, "due_timestamp": None}


class RunCyclesTests(unittest.IsolatedAsyncioTestCase):
  async def test_cycles_start_on_time_whatever_they_take(self):
    loop = asyncio.get_running_loop()
    first = loop.time()
    starts: List[float] = []

    async def cycle_function(cycle: int):
      starts.append(loop.time() - first)
      await asyncio.sleep(0.1)  # half the cycle time
      return f"read {cycle}"

    returned = await run_cycles(cycle_function, cycle_time=0.2, cycles=4)
    self.assertEqual(returned, ["read 0", "read 1", "read 2", "read 3"])
    for cycle, start in enumerate(starts):
      self.assertGreaterEqual(start, cycle * 0.2)  # never early
      self.assertLess(start, cycle * 0.2 + 0.08)  # not the 0.1 s each cycle takes, added up

  async def test_a_late_cycle_starts_at_once_with_a_warning_and_the_next_ones_keep_their_times(
    self,
  ):
    loop = asyncio.get_running_loop()
    first = loop.time()
    starts: List[float] = []

    async def cycle_function(cycle: int):
      starts.append(loop.time() - first)
      await asyncio.sleep(0.45 if cycle == 1 else 0.01)

    # Cycle 1 (due 0.2 s) ends at 0.65 s, past cycles 2 and 3 (0.4, 0.6 s); cycle 4 is due at 0.8 s.
    with self.assertLogs("pylabrobot.utils.timing", level="WARNING") as logs:
      await run_cycles(cycle_function, cycle_time=0.2, cycles=5)
    self.assertEqual(len(logs.records), 2)
    self.assertIn("cycle 2 of 5 starts", logs.output[0])
    self.assertIn("cycle 3 of 5 starts", logs.output[1])
    self.assertGreaterEqual(starts[2], starts[1] + 0.45)  # at once after cycle 1
    self.assertLess(starts[2], starts[1] + 0.45 + 0.08)
    self.assertGreaterEqual(starts[4], 0.8)  # on its time again
    self.assertLess(starts[4], 0.8 + 0.08)

  async def test_late_skip_leaves_out_a_late_cycle_and_keeps_the_next_ones_times(self):
    loop = asyncio.get_running_loop()
    first = loop.time()
    starts: Dict[int, float] = {}

    async def cycle_function(cycle: int):
      starts[cycle] = loop.time() - first
      await asyncio.sleep(0.45 if cycle == 1 else 0.01)
      return cycle

    started_cycles: List[int] = []

    def progress_callback(current: Dict[str, Any]):
      started_cycles.append(current["cycle"])
      if current["cycle"] == 0:
        raise RuntimeError("a broken progress display")

    # Cycle 1 (due 0.2 s) ends at 0.65 s, past cycles 2 and 3 (0.4, 0.6 s); cycle 4 is due at 0.8 s.
    with self.assertLogs("pylabrobot.utils.timing", level="DEBUG") as logs:
      returned = await run_cycles(
        cycle_function, cycle_time=0.2, cycles=5, late="skip", progress_callback=progress_callback
      )
    self.assertEqual(list(starts), [0, 1, 4])
    self.assertEqual(returned, [0, 1, None, None, 4])
    self.assertEqual(started_cycles, [0, 1, 4])
    messages = {
      level: [record.getMessage() for record in logs.records if record.levelname == level]
      for level in ("DEBUG", "WARNING", "ERROR")
    }
    self.assertEqual(len(messages["WARNING"]), 2)
    self.assertIn("cycle 2 of 5 skipped,", messages["WARNING"][0])
    self.assertIn("cycle 3 of 5 skipped,", messages["WARNING"][1])
    self.assertEqual(len(messages["ERROR"]), 1)  # progress_callback raised; the run went on
    self.assertRegex(
      " ".join(messages["DEBUG"]), r"cycle 1 of 5 took 0\.\d s of its 0\.2 s cycle time"
    )
    self.assertGreaterEqual(starts[4], 0.8)
    self.assertLess(starts[4], 0.8 + 0.08)

  async def test_the_current_cycle_while_a_cycle_runs(self):
    seen: List[Dict[str, Any]] = []

    async def cycle_function(cycle: int):
      seen.append(get_current_cycle())

    await run_cycles(cycle_function, cycle_time=0.02, cycles=3)
    self.assertEqual([current["cycle"] for current in seen], [0, 1, 2])
    run_start = seen[0]["run_start_timestamp"]
    self.assertIsNotNone(run_start.tzinfo)
    self.assertEqual(run_start.utcoffset(), timedelta(0))
    for cycle, current in enumerate(seen):
      self.assertEqual(current["run_start_timestamp"], run_start)
      self.assertEqual(current["due_timestamp"], run_start + timedelta(seconds=cycle * 0.02))

  async def test_an_exception_in_a_cycle_ends_the_run_and_clears_the_current_cycle(self):
    cycles: List[int] = []

    async def cycle_function(cycle: int):
      cycles.append(cycle)
      if cycle == 1:
        raise RuntimeError("stop")

    with self.assertRaises(RuntimeError):
      await run_cycles(cycle_function, cycle_time=0.01, cycles=5)
    self.assertEqual(cycles, [0, 1])
    self.assertEqual(get_current_cycle(), NO_CYCLE)

  async def test_runs_side_by_side_each_see_their_own_cycle(self):
    seen: Dict[str, List[Any]] = {"a": [], "b": []}

    def cycle_function_for(name: str, cycle_time: float):
      async def cycle_function(cycle: int):
        await asyncio.sleep(cycle_time / 2)  # the other run's cycles happen meanwhile
        seen[name].append(get_current_cycle()["cycle"])

      return cycle_function

    await asyncio.gather(
      run_cycles(cycle_function_for("a", 0.02), cycle_time=0.02, cycles=4),
      run_cycles(cycle_function_for("b", 0.03), cycle_time=0.03, cycles=3),
    )
    self.assertEqual(seen, {"a": [0, 1, 2, 3], "b": [0, 1, 2]})

  async def test_invalid_arguments_raise(self):
    async def cycle_function(cycle: int):
      pass

    for cycle_time in (0, -1, float("inf"), float("nan")):
      with self.assertRaisesRegex(ValueError, "cycle_time"):
        await run_cycles(cycle_function, cycle_time=cycle_time, cycles=1)
    for cycles in (0, -1, True):
      with self.assertRaisesRegex(ValueError, "cycles"):
        await run_cycles(cycle_function, cycle_time=1, cycles=cycles)
    late: Any = "catch up"
    with self.assertRaisesRegex(ValueError, "late"):
      await run_cycles(cycle_function, cycle_time=1, cycles=1, late=late)


if __name__ == "__main__":
  unittest.main()
