"""Bounded maintenance, per-pulse calibration, and background scheduling."""

from typing import Any
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase

from ._inco import Argument
from .device import CertusFlex, ValveCalibration
from .run import RunAborted, RunControl
from .testing_utils import FakeInco


class TestMaintenanceWorkflows(AnyioTestBase):
  """Validate the complete maintenance request before positioning or opening valves."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.io = FakeInco(strings=["CF123", "1.2.3"])
    self.device._inco = self.io  # type: ignore[assignment]
    self.moves: list[tuple[str, tuple[Argument, ...]]] = []
    self.sync_error: BaseException | None = None

    async def fake_move(command: str, *arguments: Argument) -> None:
      await anyio.lowlevel.checkpoint()
      self.moves.append((command, arguments))

    async def fake_sync_valves() -> None:
      await anyio.lowlevel.checkpoint()
      if self.sync_error is not None:
        raise self.sync_error

    async def number(path: str) -> float:
      if path.endswith("PrimeTimeout"):
        return 1e6
      if path.endswith(("Act.Pressure", "Prop.SetPressure")):
        return 0.2
      return 4.0

    self.io.number_handler = number
    stack.enter_context(patch.object(self.device, "_move", new=fake_move))
    stack.enter_context(patch.object(self.device, "_sync_valves", new=fake_sync_valves))
    await stack.enter_async_context(self.device.lifespan())
    self.io.string_calls.clear()
    self.device.valves[1].calibration = ValveCalibration(((1, 0.001), (2, 0.003)), 0.2)

  async def test_flush_is_firmware_bounded_and_parks(self) -> None:
    control = RunControl()
    await self.device.flush([1, 2], duration=0.05, control=control)
    commands = [(path.split(".")[-1], args) for path, args in self.io.proc_calls]
    assert commands == [("Prime", (50000,)), ("Prime", (50000,))]
    assert control.progress.state == "completed"
    assert control.progress.completed_doses == 2
    assert [cmd for cmd, _ in self.moves] == [
      "MoveZToPark",
      "MoveXYToPrime",
      "MoveZToPrime",
      "MoveZToPark",
      "MoveXYToPark",
    ]

  async def test_flush_over_limit_never_moves(self) -> None:
    with pytest.raises(ValueError, match="limit"):
      await self.device.flush([1, 2], duration=2)
    assert self.moves == []
    assert self.io.proc_calls == []

  async def test_volume_prime_and_purge_use_per_pulse_curve(self) -> None:
    await self.device.valves[1].prime_volume(2)
    await self.device.valves[1].purge_volume(5, pulse_volume=2)
    assert [args for _, args in self.io.proc_calls] == [(3000,), (6000, 3000), (1000, 1000)]

  async def test_uncalibrated_remainder_or_pressure_rejects_every_pulse(self) -> None:
    with pytest.raises(ValueError, match="range"):
      await self.device.valves[1].purge_volume(4.5, pulse_volume=2)
    assert self.io.proc_calls == []

    async def nan_pressure() -> float:
      return float("nan")

    with patch.object(self.device, "get_pressure", new=nan_pressure):
      with pytest.raises(RuntimeError, match="pressure"):
        await self.device.valves[1].prime_volume(1)
    assert self.io.proc_calls == []

  async def test_idle_purge_waits_for_operation_and_parks(self) -> None:
    control = RunControl()
    lock_acquired = anyio.Event()
    release_lock = anyio.Event()

    async def hold_lock() -> None:
      async with self.device._operation_lock:
        lock_acquired.set()
        await release_lock.wait()

    async with anyio.create_task_group() as tg:
      tg.start_soon(hold_lock)
      await lock_acquired.wait()
      tg.start_soon(
        lambda: self.device.idle_purge(  # type: ignore[arg-type,return-value]
          [1, 2], interval=0.001, cycles=1, duration=0.002, pulse_duration=0.001, control=control
        )
      )
      await anyio.sleep(0.01)
      assert self.moves == []
      release_lock.set()
    assert control.progress.completed_groups == 1
    assert control.progress.completed_doses == 2
    assert sum(path.endswith(".Purge") for path, _ in self.io.proc_calls) == 2

  async def test_abort_wakes_idle_interval_without_purging(self) -> None:
    control = RunControl()
    caught: RunAborted | None = None

    async def run_idle() -> None:
      nonlocal caught
      try:
        await self.device.idle_purge(
          [1, 2], interval=60, cycles=3, duration=0.002, pulse_duration=0.001, control=control
        )
      except RunAborted as exc:
        caught = exc

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_idle)
      await anyio.sleep(0.005)
      await self.device.abort()
    assert caught is not None
    assert control.progress.state == "aborted"
    assert self.moves == []
    assert not any(path.endswith(".Purge") for path, _ in self.io.proc_calls)

  async def test_purge_failure_never_issues_remainder(self) -> None:
    self.sync_error = TimeoutError("uncertain")
    with pytest.raises(TimeoutError):
      await self.device.valves[1].purge_volume(5, pulse_volume=2)
    assert sum(path.endswith(".Purge") for path, _ in self.io.proc_calls) == 1
    assert any(path.endswith(".StopDispens") for path, _ in self.io.proc_calls)
