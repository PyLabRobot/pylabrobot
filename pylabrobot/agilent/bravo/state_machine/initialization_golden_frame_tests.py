"""Golden-frame tests for InitializeTask, HomeTask, DockGripperTask, and MoveToLocationTask.

See :mod:`.golden_frame_support` for the recorder, task/engine driver, and
fixture-comparison base class this module reuses.
"""

from __future__ import annotations

import unittest

from .golden_frame_support import (
  GoldenFrameTestCase,
  new_config,
  new_controller,
  new_teachpoints,
  run_task,
)
from .tasks import DockGripperTask, HomeTask, InitializeTask, MoveToLocationTask


class InitializeTaskGoldenTests(GoldenFrameTestCase):
  async def test_cold_start_with_gripper(self):
    ctrl = new_controller(all_homed=False, gripper=True)
    task = InitializeTask(ctrl, new_config(gripper=True))
    result = await run_task(task, ctrl)
    self.assert_matches_golden("initialize_task.initialize_cold_start_with_gripper", result)

  async def test_warm_start_with_gripper(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    task = InitializeTask(ctrl, new_config(gripper=True))
    result = await run_task(task, ctrl)
    self.assert_matches_golden("initialize_task.initialize_warm_start_with_gripper", result)

  async def test_partial_cold_start_w_only(self):
    # Only W needs homing; X/Y/Z/G/Zg already homed, and Z sits above the
    # safe position -- the only path that reaches
    # InitializeTask._move_z_to_safe_position's actual retract move.
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl._axes["w"].homed = False
    ctrl._axes["z"].position = 50.0
    task = InitializeTask(ctrl, new_config(gripper=True))
    result = await run_task(task, ctrl)
    self.assert_matches_golden("initialize_task.initialize_partial_cold_start_w_only", result)

  async def test_cold_start_no_gripper(self):
    ctrl = new_controller(all_homed=False, gripper=False)
    task = InitializeTask(ctrl, new_config(gripper=False))
    result = await run_task(task, ctrl)
    self.assert_matches_golden("initialize_task.initialize_cold_start_no_gripper", result)


class HomeTaskGoldenTests(GoldenFrameTestCase):
  async def test_home_xyz_cold(self):
    ctrl = new_controller(all_homed=False, gripper=True)
    axes: list = ["x", "y", "z"]
    task = HomeTask(ctrl, new_config(gripper=True), axes)
    result = await run_task(task, ctrl)
    self.assert_matches_golden("home_task.home_xyz_cold", result)

  async def test_home_all_forced_with_gripper_dock(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    axes: list = ["x", "y", "z", "w", "g", "zg"]
    task = HomeTask(ctrl, new_config(gripper=True), axes, force=True)
    result = await run_task(task, ctrl)
    self.assert_matches_golden("home_task.home_all_forced_with_gripper_dock", result)


class DockGripperTaskGoldenTests(GoldenFrameTestCase):
  async def test_dock_gripper_no_plate(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    task = DockGripperTask(ctrl, new_config(gripper=True))
    result = await run_task(task, ctrl)
    self.assert_matches_golden("dock_gripper_task.dock_gripper_no_plate", result)

  async def test_dock_gripper_plate_detected_forced(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_plate_sensor_present(True)
    task = DockGripperTask(ctrl, new_config(gripper=True), force_if_plate_detected=True)
    result = await run_task(task, ctrl)
    self.assert_matches_golden("dock_gripper_task.dock_gripper_plate_detected_forced", result)


class MoveToLocationTaskGoldenTests(GoldenFrameTestCase):
  async def test_move_to_location_full_with_approach(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    task = MoveToLocationTask(ctrl, new_teachpoints(), 3, safe_z_position=0.0, approach_height=10.0)
    result = await run_task(task, ctrl)
    self.assert_matches_golden("move_to_location_task.move_to_location_full_with_approach", result)

  async def test_move_to_location_z_only(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    task = MoveToLocationTask(ctrl, new_teachpoints(), 3, safe_z_position=0.0, only_move_z=True)
    result = await run_task(task, ctrl)
    self.assert_matches_golden("move_to_location_task.move_to_location_z_only", result)


if __name__ == "__main__":
  unittest.main()
