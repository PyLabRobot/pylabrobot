"""Boundary pauses, abort races, and controller issue decoding."""

from typing import Any, Callable, Optional
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase

from ._inco import Argument
from .device import CertusFlex, CertusFlexError
from .geometry_tests import registration
from .plans import DispensePlan, DispenseStep
from .run import RunAborted, RunControl
from .testing_utils import FakeInco


class TestRunControl(AnyioTestBase):
  """Ensure controls never replay a dose or fire after an abort during motion."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.io = FakeInco(strings=["C196069", "1.0.7.378-trunk"], default_number=4.0)
    self.device._inco = self.io  # type: ignore[assignment]
    self.moves: list[tuple[str, tuple[Argument, ...]]] = []
    self.registered_moves: list[tuple[Any, ...]] = []
    self.move_registered_hook: Optional[Callable[..., Any]] = None
    self.sync_valves_hook: Optional[Callable[[], Any]] = None

    async def fake_check_plan(plan: DispensePlan) -> None:
      await anyio.lowlevel.checkpoint()

    async def fake_move_registered(*args: Any) -> None:
      await anyio.lowlevel.checkpoint()
      self.registered_moves.append(args)
      if self.move_registered_hook is not None:
        await self.move_registered_hook(*args)

    async def fake_move(command: str, *arguments: Argument) -> None:
      await anyio.lowlevel.checkpoint()
      self.moves.append((command, arguments))

    async def fake_sync_valves() -> None:
      await anyio.lowlevel.checkpoint()
      if self.sync_valves_hook is not None:
        await self.sync_valves_hook()

    stack.enter_context(patch.object(self.device, "_check_plan", new=fake_check_plan))
    stack.enter_context(patch.object(self.device, "_move_registered", new=fake_move_registered))
    stack.enter_context(patch.object(self.device, "_move", new=fake_move))
    stack.enter_context(patch.object(self.device, "_sync_valves", new=fake_sync_valves))
    await stack.enter_async_context(self.device.lifespan())
    self.io.string_calls.clear()
    reg = registration()
    self.plan = self.device.plan_dispense(
      reg, [DispenseStep(reg.plate.get_item(w), 1, duration=0.001) for w in ("B2", "B3")]
    )

  async def test_pause_finishes_current_group_and_resumes_once(self) -> None:
    control = RunControl()

    async def barrier() -> None:
      if control.progress.completed_groups == 0:
        control.pause()

    self.sync_valves_hook = barrier
    confirmed = 0

    async def run_plan() -> None:
      nonlocal confirmed
      confirmed = await self.device.execute_plan(self.plan, control=control)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_plan)
      with anyio.fail_after(1.0):
        await control.wait_paused()
      assert control.progress.completed_doses == 1
      assert control.progress.uncertain_group is None
      assert len(self.moves) == 1
      control.resume()
    assert confirmed == 2
    assert control.progress.state == "completed"
    assert sum(path.endswith(".Dispens") for path, _ in self.io.proc_calls) == 2
    with pytest.raises(ValueError):
      control.resume()
    with pytest.raises(ValueError):
      await self.device.execute_plan(self.plan, control=control)

  async def test_abort_during_motion_prevents_dose_and_further_motion(self) -> None:
    entered, release = anyio.Event(), anyio.Event()

    async def moving(*args: Any) -> None:
      entered.set()
      await release.wait()

    self.move_registered_hook = moving
    control = RunControl()
    caught: RunAborted | None = None

    async def run_plan() -> None:
      nonlocal caught
      try:
        await self.device.execute_plan(self.plan, control=control)
      except RunAborted as exc:
        caught = exc

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_plan)
      with anyio.fail_after(1.0):
        await entered.wait()
      await self.device.abort()
      release.set()
    assert caught is not None
    assert control.progress.state == "aborted"
    assert control.progress.uncertain_group is None
    assert not any(path.endswith(".Dispens") for path, _ in self.io.proc_calls)
    assert sum(path.endswith(".CloseValve") for path, _ in self.io.proc_calls) == 8
    assert self.moves == []

  async def test_abort_during_dose_preserves_uncertain_group(self) -> None:
    entered, release = anyio.Event(), anyio.Event()

    async def command(path: str, *args: Argument) -> None:
      if path.endswith(".Dispens"):
        entered.set()
        await release.wait()

    self.io.call_handler = command
    control = RunControl()
    caught: RunAborted | None = None

    async def run_plan() -> None:
      nonlocal caught
      try:
        await self.device.execute_plan(self.plan, control=control)
      except RunAborted as exc:
        caught = exc

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_plan)
      with anyio.fail_after(1.0):
        await entered.wait()
      await self.device.abort()
      release.set()
    assert caught is not None
    assert control.progress.uncertain_group == 0
    assert control.progress.completed_doses == 0
    assert sum(path.endswith(".Dispens") for path, _ in self.io.proc_calls) == 1

  async def test_cancellation_during_dose_stops_outputs_and_clears_active_run(self) -> None:
    entered = anyio.Event()

    async def command(path: str, *args: Argument) -> None:
      if path.endswith(".Dispens"):
        entered.set()
        await anyio.Event().wait()

    self.io.call_handler = command
    control = RunControl()
    scope = anyio.CancelScope()

    async def run_plan() -> None:
      with scope:
        await self.device.execute_plan(self.plan, control=control)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_plan)
      with anyio.fail_after(1.0):
        await entered.wait()
      scope.cancel()
    assert scope.cancelled_caught
    assert self.device._active_run is None
    assert any(path.endswith(".StopDispens") for path, _ in self.io.proc_calls)

  async def test_abort_wakes_paused_run(self) -> None:
    control = RunControl()
    control.pause()
    caught: RunAborted | None = None

    async def run_plan() -> None:
      nonlocal caught
      try:
        await self.device.execute_plan(self.plan, control=control)
      except RunAborted as exc:
        caught = exc

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_plan)
      with anyio.fail_after(1.0):
        await control.wait_paused()
      await self.device.abort()
    assert caught is not None
    assert self.registered_moves == []
    with pytest.raises(ValueError, match="aborted"):
      control.pause()

  async def test_resume_then_immediate_pause_keeps_executor_paused(self) -> None:
    """Re-pausing in the same scheduler tick after resume() does not launch the next group."""
    control = RunControl()
    control.pause()
    confirmed = 0

    async def run_plan() -> None:
      nonlocal confirmed
      confirmed = await self.device.execute_plan(self.plan, control=control)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_plan)
      with anyio.fail_after(1.0):
        await control.wait_paused()
      control.resume()
      control.pause()
      await anyio.sleep(0.02)
      assert control.progress.state == "paused"
      assert self.registered_moves == []
      control.resume()
    assert confirmed == 2

  async def test_pause_during_final_group_unblocks_wait_paused_on_completion(self) -> None:
    """A pause requested during the final group raises instead of hanging wait_paused()."""
    control = RunControl()

    async def pause_on_last_group() -> None:
      if control.progress.completed_groups == 1:
        control.pause()

    async def run_plan() -> None:
      await self.device.execute_plan(self.plan, control=control)

    self.sync_valves_hook = pause_on_last_group

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_plan)
      with anyio.fail_after(1.0):
        with pytest.raises(RuntimeError, match="before reaching a pause"):
          await control.wait_paused()

  async def test_flush_on_inactive_device_preserves_run_control(self) -> None:
    """Calling flush() on an inactive CertusFlex fails before claiming RunControl."""
    inactive = CertusFlex("example.invalid", head="flex8_angled")
    control = RunControl()
    with pytest.raises(RuntimeError, match="not active"):
      await inactive.flush([0], duration=0.05, control=control)
    assert control.progress.state == "pending"

  async def test_close_failure_is_not_reported_as_success(self) -> None:
    async def fail_call(path: str, *args: Argument) -> None:
      raise TimeoutError("disconnected")

    self.io.call_handler = fail_call
    with pytest.raises(CertusFlexError, match="Could not confirm"):
      await self.device.close_all()
    assert len(self.io.proc_calls) == 16

  async def test_fault_details(self) -> None:
    numbers = iter([1.0, 42.0, 1234.0, 5.0, 45000.0])

    async def next_num(path: str) -> float:
      return next(numbers)

    self.io.number_handler = next_num
    self.io.strings = ["Issue0", "PressureRegulator", "Pressure not reached"]
    (fault,) = await self.device.get_faults()
    assert (fault.identifier, fault.code, fault.flags) == (42, 1234, 5)
    assert fault.text == "Pressure not reached"
    assert fault.task == "PressureRegulator"
    assert self.io.proc_calls == []
