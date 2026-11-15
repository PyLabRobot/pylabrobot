"""Whole-plan validation and coordinated multi-valve execution."""

import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from ._inco_udp import IncoUDP
from .device import CertusFlex, ValveCalibration
from .geometry_tests import registration
from .plans import DispenseStep


class PlanTests(unittest.IsolatedAsyncioTestCase):
  """Exercise cumulative capacity, ordering, grouping, and failed completion."""

  def setUp(self) -> None:
    self.reg = registration()
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.device.serial_number = "C196069"
    self.io = MagicMock(spec=IncoUDP)
    self.device._inco = self.io
    move_registered_patch = patch.object(self.device, "_move_registered", new_callable=AsyncMock)
    self.move_registered: AsyncMock = move_registered_patch.start()
    self.addCleanup(move_registered_patch.stop)
    move_patch = patch.object(self.device, "_move", new_callable=AsyncMock)
    self.move: AsyncMock = move_patch.start()
    self.addCleanup(move_patch.stop)
    sync_valves_patch = patch.object(self.device, "_sync_valves", new_callable=AsyncMock)
    self.sync_valves: AsyncMock = sync_valves_patch.start()
    self.addCleanup(sync_valves_patch.stop)

    async def number(path: str) -> float:
      return 0.2 if path.endswith(("Act.Pressure", "Prop.SetPressure")) else 4.0

    self.io.get_number.side_effect = number
    for valve in self.device.valves:
      valve.calibration = ValveCalibration(((1, 0.001), (500, 0.1)), pressure=0.2)

  def test_grouping_preserves_order_and_unique_outputs(self) -> None:
    steps = [
      DispenseStep(self.reg.plate.get_item(w), c, duration=0.001)
      for w, c in (("B2", 1), ("C2", 2), ("B2", 1), ("B3", 1))
    ]
    plan = self.device.plan_dispense(self.reg, steps, repeats=2)
    self.assertEqual([len(g) for g in plan.groups], [2, 1, 1, 2, 1, 1])
    self.assertEqual(plan.count, 8)
    self.assertEqual(len(self.device.plan_dispense(self.reg, steps, parallel=False).groups), 4)

  def test_capacity_counts_all_channels_and_repeats(self) -> None:
    step = DispenseStep(self.reg.plate.get_item("B2"), 1, volume=200)
    with self.assertRaisesRegex(ValueError, "capacity"):
      self.device.plan_dispense(self.reg, [step], repeats=2)
    self.io.call.assert_not_called()

  def test_gradient_and_row_order(self) -> None:
    wells = [self.reg.plate.get_item(w) for w in ("C3", "B2", "B3")]
    steps = DispenseStep.gradient(wells, channel=1, start=1, end=3)
    self.assertEqual([s.volume for s in steps], [1, 2, 3])
    plan = self.device.plan_dispense(self.reg, steps, order="row")
    self.assertEqual([p.step.well for g in plan.groups for p in g], [wells[1], wells[2], wells[0]])

  async def test_group_uses_one_barrier(self) -> None:
    steps = [
      DispenseStep(self.reg.plate.get_item(w), c, duration=0.001) for w, c in (("B2", 1), ("C2", 2))
    ]
    result = await self.device.execute_plan(self.device.plan_dispense(self.reg, steps))
    self.assertEqual(result, 2)
    self.sync_valves.assert_awaited_once()
    self.assertEqual(
      self.io.call.call_args_list,
      [
        call("Machine.Entity.Dispenser.Module.Entity.Valve1.Cmd.Dispens", 1000),
        call("Machine.Entity.Dispenser.Module.Entity.Valve2.Cmd.Dispens", 1000),
      ],
    )

  async def test_lost_group_reply_stops_without_repeating(self) -> None:
    steps = [
      DispenseStep(self.reg.plate.get_item(w), c, duration=0.001) for w, c in (("B2", 1), ("C2", 2))
    ]
    self.sync_valves.side_effect = TimeoutError("uncertain dose")
    with self.assertRaisesRegex(TimeoutError, "uncertain dose"):
      await self.device.execute_plan(self.device.plan_dispense(self.reg, steps))
    self.assertEqual(sum(c.args[0].endswith(".Dispens") for c in self.io.call.call_args_list), 2)
    self.assertEqual(
      sum(c.args[0].endswith(".StopDispens") for c in self.io.call.call_args_list), 2
    )
    self.move.assert_not_awaited()

  async def test_changed_calibration_rejects_before_motion(self) -> None:
    step = DispenseStep(self.reg.plate.get_item("B2"), 1, volume=2)
    plan = self.device.plan_dispense(self.reg, [step])
    self.device.valves[1].calibration = ValveCalibration(((1, 0.002), (500, 0.2)), pressure=0.2)
    with self.assertRaisesRegex(ValueError, "changed"):
      await self.device.execute_plan(plan)
    self.move_registered.assert_not_awaited()
