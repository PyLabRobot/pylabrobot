"""Bounded maintenance, per-pulse calibration, and background scheduling."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ._inco_udp import IncoUDP
from .device import CertusFlex, ValveCalibration
from .run import RunAborted, RunControl


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
  """Validate the complete maintenance request before positioning or opening valves."""

  def setUp(self) -> None:
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.io = MagicMock(spec=IncoUDP)
    self.device._inco = self.io
    move_patch = patch.object(self.device, "_move", new_callable=AsyncMock)
    self.move: AsyncMock = move_patch.start()
    self.addCleanup(move_patch.stop)
    sync_valves_patch = patch.object(self.device, "_sync_valves", new_callable=AsyncMock)
    self.sync_valves: AsyncMock = sync_valves_patch.start()
    self.addCleanup(sync_valves_patch.stop)

    async def number(path: str) -> float:
      if path.endswith("PrimeTimeout"):
        return 1e6
      if path.endswith(("Act.Pressure", "Prop.SetPressure")):
        return 0.2
      return 4

    self.io.get_number.side_effect = number
    self.device.valves[1].calibration = ValveCalibration(((1, 0.001), (2, 0.003)), 0.2)

  async def test_flush_is_firmware_bounded_and_parks(self) -> None:
    control = RunControl()
    await self.device.flush([1, 2], duration=0.05, control=control)
    commands = [(c.args[0].split(".")[-1], c.args[1:]) for c in self.io.call.call_args_list]
    self.assertEqual(commands, [("Prime", (50000,)), ("Prime", (50000,))])
    self.assertEqual(control.progress.state, "completed")
    self.assertEqual(control.progress.completed_doses, 2)
    self.assertEqual(
      [c.args[0] for c in self.move.call_args_list],
      ["MoveZToPark", "MoveXYToPrime", "MoveZToPrime", "MoveZToPark", "MoveXYToPark"],
    )

  async def test_flush_over_limit_never_moves(self) -> None:
    with self.assertRaisesRegex(ValueError, "limit"):
      await self.device.flush([1, 2], duration=2)
    self.move.assert_not_awaited()
    self.io.call.assert_not_awaited()

  async def test_volume_prime_and_purge_use_per_pulse_curve(self) -> None:
    await self.device.valves[1].prime_volume(2)
    await self.device.valves[1].purge_volume(5, pulse_volume=2)
    self.assertEqual(
      [c.args[1:] for c in self.io.call.call_args_list], [(3000,), (6000, 3000), (1000, 1000)]
    )

  async def test_uncalibrated_remainder_or_pressure_rejects_every_pulse(self) -> None:
    with self.assertRaisesRegex(ValueError, "range"):
      await self.device.valves[1].purge_volume(4.5, pulse_volume=2)
    self.io.call.assert_not_awaited()
    with patch.object(self.device, "get_pressure", return_value=float("nan")):
      with self.assertRaisesRegex(RuntimeError, "pressure"):
        await self.device.valves[1].prime_volume(1)
    self.io.call.assert_not_awaited()

  async def test_idle_purge_waits_for_operation_and_parks(self) -> None:
    await self.device._operation_lock.acquire()
    control = RunControl()
    task = asyncio.create_task(
      self.device.idle_purge(
        [1, 2], interval=0.001, cycles=1, duration=0.002, pulse_duration=0.001, control=control
      )
    )
    await asyncio.sleep(0.01)
    self.move.assert_not_awaited()
    self.device._operation_lock.release()
    await asyncio.wait_for(task, 1)
    self.assertEqual(control.progress.completed_groups, 1)
    self.assertEqual(control.progress.completed_doses, 2)
    self.assertEqual(sum(c.args[0].endswith(".Purge") for c in self.io.call.call_args_list), 2)

  async def test_abort_wakes_idle_interval_without_purging(self) -> None:
    control = RunControl()
    task = asyncio.create_task(
      self.device.idle_purge(
        [1, 2], interval=60, cycles=3, duration=0.002, pulse_duration=0.001, control=control
      )
    )
    await asyncio.sleep(0)
    await self.device.abort()
    with self.assertRaises(RunAborted):
      await asyncio.wait_for(task, 1)
    self.assertEqual(control.progress.state, "aborted")
    self.move.assert_not_awaited()
    self.assertFalse(any(c.args[0].endswith(".Purge") for c in self.io.call.call_args_list))

  async def test_purge_failure_never_issues_remainder(self) -> None:
    self.sync_valves.side_effect = TimeoutError("uncertain")
    with self.assertRaises(TimeoutError):
      await self.device.valves[1].purge_volume(5, pulse_volume=2)
    self.assertEqual(sum(c.args[0].endswith(".Purge") for c in self.io.call.call_args_list), 1)
