"""Boundary pauses, abort races, and controller issue decoding."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ._inco_udp import IncoUDP
from .device import CertusFlex, CertusFlexError
from .geometry_tests import registration
from .plans import DispenseStep
from .run import RunAborted, RunControl


class RunTests(unittest.IsolatedAsyncioTestCase):
  """Ensure controls never replay a dose or fire after an abort during motion."""

  def setUp(self) -> None:
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.device.serial_number = "C196069"
    self.io = MagicMock(spec=IncoUDP)
    self.device._inco = self.io
    check_plan_patch = patch.object(self.device, "_check_plan", new_callable=AsyncMock)
    self.check_plan: AsyncMock = check_plan_patch.start()
    self.addCleanup(check_plan_patch.stop)
    move_registered_patch = patch.object(self.device, "_move_registered", new_callable=AsyncMock)
    self.move_registered: AsyncMock = move_registered_patch.start()
    self.addCleanup(move_registered_patch.stop)
    move_patch = patch.object(self.device, "_move", new_callable=AsyncMock)
    self.move: AsyncMock = move_patch.start()
    self.addCleanup(move_patch.stop)
    sync_valves_patch = patch.object(self.device, "_sync_valves", new_callable=AsyncMock)
    self.sync_valves: AsyncMock = sync_valves_patch.start()
    self.addCleanup(sync_valves_patch.stop)
    self.io.get_number.return_value = 4.0
    reg = registration()
    self.plan = self.device.plan_dispense(
      reg, [DispenseStep(reg.plate.get_item(w), 1, duration=0.001) for w in ("B2", "B3")]
    )

  async def test_pause_finishes_current_group_and_resumes_once(self) -> None:
    control = RunControl()

    async def barrier() -> None:
      if control.progress.completed_groups == 0:
        control.pause()

    self.sync_valves.side_effect = barrier
    task = asyncio.create_task(self.device.execute_plan(self.plan, control=control))
    await asyncio.wait_for(control.wait_paused(), 1)
    self.assertEqual(control.progress.completed_doses, 1)
    self.assertIsNone(control.progress.uncertain_group)
    self.assertEqual(self.move.await_count, 1)
    control.resume()
    self.assertEqual(await asyncio.wait_for(task, 1), 2)
    self.assertEqual(control.progress.state, "completed")
    self.assertEqual(sum(c.args[0].endswith(".Dispens") for c in self.io.call.call_args_list), 2)
    with self.assertRaises(ValueError):
      control.resume()
    with self.assertRaises(ValueError):
      await self.device.execute_plan(self.plan, control=control)

  async def test_abort_during_motion_prevents_dose_and_further_motion(self) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def moving(*args) -> None:
      entered.set()
      await release.wait()

    self.move_registered.side_effect = moving
    control = RunControl()
    task = asyncio.create_task(self.device.execute_plan(self.plan, control=control))
    await asyncio.wait_for(entered.wait(), 1)
    await self.device.abort()
    release.set()
    with self.assertRaises(RunAborted):
      await asyncio.wait_for(task, 1)
    self.assertEqual(control.progress.state, "aborted")
    self.assertIsNone(control.progress.uncertain_group)
    self.assertFalse(any(c.args[0].endswith(".Dispens") for c in self.io.call.call_args_list))
    self.assertEqual(sum(c.args[0].endswith(".CloseValve") for c in self.io.call.call_args_list), 8)
    self.move.assert_not_awaited()

  async def test_abort_during_dose_preserves_uncertain_group(self) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def command(path: str, *args) -> None:
      if path.endswith(".Dispens"):
        entered.set()
        await release.wait()

    self.io.call.side_effect = command
    control = RunControl()
    task = asyncio.create_task(self.device.execute_plan(self.plan, control=control))
    await asyncio.wait_for(entered.wait(), 1)
    await self.device.abort()
    release.set()
    with self.assertRaises(RunAborted):
      await asyncio.wait_for(task, 1)
    self.assertEqual(control.progress.uncertain_group, 0)
    self.assertEqual(control.progress.completed_doses, 0)
    self.assertEqual(sum(c.args[0].endswith(".Dispens") for c in self.io.call.call_args_list), 1)

  async def test_abort_wakes_paused_run(self) -> None:
    control = RunControl()
    control.pause()
    task = asyncio.create_task(self.device.execute_plan(self.plan, control=control))
    await asyncio.wait_for(control.wait_paused(), 1)
    await self.device.abort()
    with self.assertRaises(RunAborted):
      await asyncio.wait_for(task, 1)
    self.move_registered.assert_not_awaited()

  async def test_close_failure_is_not_reported_as_success(self) -> None:
    self.io.call.side_effect = TimeoutError("disconnected")
    with self.assertRaisesRegex(CertusFlexError, "Could not confirm"):
      await self.device.close_all()
    self.assertEqual(self.io.call.await_count, 16)

  async def test_fault_details(self) -> None:
    self.io.get_number.side_effect = [1, 42, 1234, 5, 45000]
    self.io.get_string.side_effect = ["Issue0", "PressureRegulator", "Pressure not reached"]
    (fault,) = await self.device.get_faults()
    self.assertEqual((fault.identifier, fault.code, fault.flags), (42, 1234, 5))
    self.assertEqual(fault.text, "Pressure not reached")
    self.assertEqual(fault.task, "PressureRegulator")
    self.io.call.assert_not_awaited()
