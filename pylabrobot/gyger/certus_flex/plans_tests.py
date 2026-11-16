"""Whole-plan validation and coordinated multi-valve execution."""

from typing import Any
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase

from ._inco import Argument
from .device import CertusFlex, ValveCalibration
from .geometry_tests import registration
from .plans import DispenseStep
from .testing_utils import FakeInco


class TestDispensePlans(AnyioTestBase):
  """Exercise cumulative capacity, ordering, grouping, and failed completion."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.reg = registration()
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.io = FakeInco(strings=["C196069", "1.0.7.378-trunk"])
    self.device._inco = self.io  # type: ignore[assignment]
    self.registered_moves: list[tuple[Any, ...]] = []
    self.moves: list[tuple[str, tuple[Argument, ...]]] = []
    self.sync_count = 0
    self.sync_error: BaseException | None = None

    async def fake_move_registered(*args: Any) -> None:
      await anyio.lowlevel.checkpoint()
      self.registered_moves.append(args)

    async def fake_move(command: str, *arguments: Argument) -> None:
      await anyio.lowlevel.checkpoint()
      self.moves.append((command, arguments))

    async def fake_sync_valves() -> None:
      await anyio.lowlevel.checkpoint()
      self.sync_count += 1
      if self.sync_error is not None:
        raise self.sync_error

    async def number(path: str) -> float:
      return 0.2 if path.endswith(("Act.Pressure", "Prop.SetPressure")) else 4.0

    self.io.number_handler = number
    stack.enter_context(patch.object(self.device, "_move_registered", new=fake_move_registered))
    stack.enter_context(patch.object(self.device, "_move", new=fake_move))
    stack.enter_context(patch.object(self.device, "_sync_valves", new=fake_sync_valves))
    await stack.enter_async_context(self.device.lifespan())
    for valve in self.device.valves:
      valve.calibration = ValveCalibration(((1, 0.001), (500, 0.1)), pressure=0.2)

  def test_grouping_preserves_order_and_unique_outputs(self) -> None:
    steps = [
      DispenseStep(self.reg.plate.get_item(w), c, duration=0.001)
      for w, c in (("B2", 1), ("C2", 2), ("B2", 1), ("B3", 1))
    ]
    plan = self.device.plan_dispense(self.reg, steps, repeats=2)
    assert [len(g) for g in plan.groups] == [2, 1, 1, 2, 1, 1]
    assert plan.count == 8
    assert len(self.device.plan_dispense(self.reg, steps, parallel=False).groups) == 4

  def test_capacity_counts_all_channels_and_repeats(self) -> None:
    step = DispenseStep(self.reg.plate.get_item("B2"), 1, volume=200)
    with pytest.raises(ValueError, match="capacity"):
      self.device.plan_dispense(self.reg, [step], repeats=2)
    assert self.io.proc_calls == []

  def test_gradient_and_row_order(self) -> None:
    wells = [self.reg.plate.get_item(w) for w in ("C3", "B2", "B3")]
    steps = DispenseStep.gradient(wells, channel=1, start=1, end=3)
    assert [s.volume for s in steps] == [1, 2, 3]
    plan = self.device.plan_dispense(self.reg, steps, order="row")
    assert [p.step.well for g in plan.groups for p in g] == [wells[1], wells[2], wells[0]]

  async def test_group_uses_one_barrier(self) -> None:
    steps = [
      DispenseStep(self.reg.plate.get_item(w), c, duration=0.001) for w, c in (("B2", 1), ("C2", 2))
    ]
    result = await self.device.execute_plan(self.device.plan_dispense(self.reg, steps))
    assert result == 2
    assert self.sync_count == 1
    assert self.io.proc_calls == [
      ("Machine.Entity.Dispenser.Module.Entity.Valve1.Cmd.Dispens", (1000,)),
      ("Machine.Entity.Dispenser.Module.Entity.Valve2.Cmd.Dispens", (1000,)),
    ]

  async def test_lost_group_reply_stops_without_repeating(self) -> None:
    steps = [
      DispenseStep(self.reg.plate.get_item(w), c, duration=0.001) for w, c in (("B2", 1), ("C2", 2))
    ]
    self.sync_error = TimeoutError("uncertain dose")
    with pytest.raises(TimeoutError, match="uncertain dose"):
      await self.device.execute_plan(self.device.plan_dispense(self.reg, steps))
    assert sum(path.endswith(".Dispens") for path, _ in self.io.proc_calls) == 2
    assert sum(path.endswith(".StopDispens") for path, _ in self.io.proc_calls) == 2
    assert self.moves == []

  async def test_changed_calibration_rejects_before_motion(self) -> None:
    step = DispenseStep(self.reg.plate.get_item("B2"), 1, volume=2)
    plan = self.device.plan_dispense(self.reg, [step])
    self.device.valves[1].calibration = ValveCalibration(((1, 0.002), (500, 0.2)), pressure=0.2)
    with pytest.raises(ValueError, match="changed"):
      await self.device.execute_plan(plan)
    assert self.registered_moves == []
