"""Golden-frame tests for PickPlaceTask, GripperTeachMoveTask, DelidPlateTask,
RelidPlateTask, and ScanStackHeightTask.

See :mod:`.golden_frame_support` for the recorder, task/engine driver, and
fixture-comparison base class this module reuses.
"""

from __future__ import annotations

import unittest

from ..config import BravoMachineConfig
from ..deck.labware import DeckState, Labware
from .engine import ErrorAction
from .golden_frame_support import (
  GoldenFrameTestCase,
  new_config,
  new_controller,
  new_teachpoints,
  run_task,
)
from .tasks import (
  DelidPlateTask,
  GripperTeachMoveTask,
  PickPlaceTask,
  RelidPlateTask,
  ScanStackHeightTask,
)


def _plate(
  id_: str = "lw-plate",
  name: str = "Plate",
  height: float = 14.5,
  stack_height: float = 14.5,
  gripper_offset: float = 5.0,
  **kwargs,
) -> Labware:
  kwargs.setdefault("metadata", {})
  return Labware(
    id=id_,
    name=name,
    height=height,
    width=85.5,
    length=127.5,
    stack_height=stack_height,
    gripper_offset=gripper_offset,
    wells=96,
    **kwargs,
  )


def _config() -> BravoMachineConfig:
  config = new_config(gripper=True)
  config.head.teach_tip_length_mm = 26.1
  return config


def _lidded_plate() -> Labware:
  metadata = {
    "length_mm": 127.5,
    "width_mm": 85.5,
    "base_height_mm": 14.5,
    "lidded_height_mm": 22.0,
    "lid_resting_height_mm": 7.5,
    "height_mm": 22.0,
  }
  return Labware(
    id="lw-lidded",
    name="Lidded Plate",
    height=22.0,
    width=85.5,
    length=127.5,
    stack_height=22.0,
    gripper_offset=5.0,
    wells=96,
    is_lidded=True,
    metadata=metadata,
  )


class PickPlaceTaskGoldenTests(GoldenFrameTestCase):
  async def test_basic(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    deck.add(3, _plate())
    task = PickPlaceTask(ctrl, new_teachpoints(), config, deck, 3, 6, speed="med")
    result = await run_task(task, ctrl)
    self.assert_matches_golden("pick_place_task.pick_place_basic", result)

  async def test_pickup_verification_failure(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    deck.add(3, _plate())
    original_grip = ctrl.grip

    def failing_grip(speed, position, grip_lid=False):
      ctrl._record("grip", speed=speed, position=position, grip_lid=grip_lid)
      ctrl._axes["g"].position = 12.0  # past _PICKUP_FAILURE_G_THRESHOLD_MM

    ctrl.grip = failing_grip  # type: ignore[method-assign]
    task = PickPlaceTask(ctrl, new_teachpoints(), config, deck, 3, 6, speed="med")
    try:
      result = await run_task(task, ctrl, choice_fn=lambda t: ErrorAction.ABORT)
    finally:
      ctrl.grip = original_grip  # type: ignore[method-assign]
    self.assert_matches_golden("pick_place_task.pick_place_pickup_verification_failure", result)

  async def test_mounted_group(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    collection = _plate(
      id_="lw-collection",
      name="Collection Plate",
      height=14.5,
      stack_height=14.5,
      gripper_offset=5.0,
    )
    filter_plate = _plate(
      id_="lw-filter",
      name="Filter Plate",
      height=10.0,
      stack_height=10.0,
      gripper_offset=3.0,
      is_mounted=True,
    )
    deck.add_mounted_group(3, [filter_plate, collection])
    task = PickPlaceTask(ctrl, new_teachpoints(), config, deck, 3, 6, speed="med")
    result = await run_task(task, ctrl)
    self.assert_matches_golden("pick_place_task.pick_place_mounted_group", result)


class GripperTeachMoveTaskGoldenTests(GoldenFrameTestCase):
  async def test_basic(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    deck.add(3, _plate())
    task = GripperTeachMoveTask(ctrl, new_teachpoints(), config, deck, 3, approach_height=5.0)
    result = await run_task(task, ctrl)
    self.assert_matches_golden("gripper_teach_move_task.gripper_teach_move_basic", result)


class DelidPlateTaskGoldenTests(GoldenFrameTestCase):
  async def test_basic(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    deck.add(3, _lidded_plate())
    task = DelidPlateTask(ctrl, new_teachpoints(), config, deck, 3, 6, speed="med")
    result = await run_task(task, ctrl)
    self.assert_matches_golden("delid_plate_task.delid_plate_basic", result)


class RelidPlateTaskGoldenTests(GoldenFrameTestCase):
  async def test_basic(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    lid_meta = {
      "base_class": "lid",
      "kind": "lid",
      "length_mm": 127.5,
      "width_mm": 85.5,
      "height_mm": 7.5,
      "stack_height_mm": 7.5,
      "lid_gripper_offset_mm": 2.0,
    }
    lid = Labware(
      id="lw-lid",
      name="Standalone Lid",
      height=7.5,
      width=85.5,
      length=127.5,
      stack_height=7.5,
      gripper_offset=2.0,
      labware_type="lid",
      metadata=lid_meta,
    )
    deck.add(3, lid)
    bare_plate = _plate(
      id_="lw-bare",
      name="Bare Plate",
      height=14.5,
      stack_height=14.5,
      gripper_offset=5.0,
      metadata={"can_have_lid": True},
    )
    deck.add(6, bare_plate)
    task = RelidPlateTask(ctrl, new_teachpoints(), config, deck, 3, 6, speed="med")
    result = await run_task(task, ctrl)
    self.assert_matches_golden("relid_plate_task.relid_plate_basic", result)


class ScanStackHeightTaskGoldenTests(GoldenFrameTestCase):
  async def test_simulation_completed_count_matches(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    template = _plate(id_="lw-tmpl", name="Template Plate", height=14.5, stack_height=14.5)
    for _ in range(3):
      deck.add(3, _plate(id_="lw-stack", name="Stacked Plate", height=14.5, stack_height=14.5))
    task = ScanStackHeightTask(
      ctrl, new_teachpoints(), config, deck, location=3, template_labware=template, expected_count=3
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden(
      "scan_stack_height_task.scan_simulation_completed_count_matches", result
    )

  async def test_simulation_count_mismatch(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    config = _config()
    deck = DeckState()
    template = _plate(id_="lw-tmpl", name="Template Plate", height=14.5, stack_height=14.5)
    for _ in range(2):
      deck.add(3, _plate(id_="lw-stack", name="Stacked Plate", height=14.5, stack_height=14.5))
    task = ScanStackHeightTask(
      ctrl, new_teachpoints(), config, deck, location=3, template_labware=template, expected_count=5
    )
    result = await run_task(task, ctrl, choice_fn=lambda t: ErrorAction.ABORT)
    self.assert_matches_golden("scan_stack_height_task.scan_simulation_count_mismatch", result)


if __name__ == "__main__":
  unittest.main()
