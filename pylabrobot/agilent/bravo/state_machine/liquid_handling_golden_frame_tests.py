"""Golden-frame tests for AspirateTask, DispenseTask, and MixTask.

See :mod:`.golden_frame_support` for the recorder, task/engine driver, and
fixture-comparison base class this module reuses. Every scenario here runs
against a :class:`~..controllers.simulation.SimulationController`, whose W
axis is microlitre-native like the Agile family. Each task combines the
controller's current W position with a converted volume delta itself (see
``_w_axis_motion_value`` in :mod:`.tasks`) before handing the result to a
move; on this microlitre-native controller that conversion is an identity,
so it is exercised here as a no-op. A W move captured against a Darwin
controller would show a millimetre-valued position where these fixtures
show a microlitre-valued one, reflecting that same conversion with a
non-identity factor -- not a capture mismatch. :mod:`.tasks_tests` pins
that Darwin-specific conversion directly, independent of this module's
fixtures.
"""

from __future__ import annotations

import unittest

from ..deck.labware import DeckState, Labware
from ..head_mode import PlateSelection, normalize_head_mode
from .engine import ErrorAction
from .golden_frame_support import GoldenFrameTestCase, new_controller, new_teachpoints, run_task
from .tasks import AspirateTask, DispenseTask, MixTask


def _plate() -> Labware:
  return Labware(
    id="lw-plate96",
    name="Test 96-well Plate",
    height=14.5,
    width=85.5,
    length=127.5,
    wells=96,
    metadata={
      "rows": 8,
      "cols": 12,
      "spacing_x_mm": 9.0,
      "spacing_y_mm": 9.0,
      "well_depth_mm": 10.86,
      "well_diameter_mm": 6.86,
    },
  )


class AspirateTaskGoldenTests(GoldenFrameTestCase):
  async def test_simple_fixed_tip_no_labware(self):
    # 96_f_50 is a fixed-tip head: no tip-length bookkeeping required.
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_f_50")
    task = AspirateTask(ctrl, new_teachpoints(), 3, volume=50.0, head_type="96_f_50")
    result = await run_task(task, ctrl)
    self.assert_matches_golden("aspirate_task.aspirate_simple_fixed_tip_no_labware", result)

  async def test_full_disposable_with_pre_post_tip_touch(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = AspirateTask(
      ctrl,
      new_teachpoints(),
      5,
      volume=50.0,
      pre_aspirate_volume=5.0,
      post_aspirate_volume=3.0,
      tip_touch=True,
      head_type="96_d_70",
      head_mode=mode,
      plate_selection=PlateSelection(location=5, row=0, col=0),
      labware=_plate(),
      teach_tip_length_mm=26.1,
      attached_tip_length_mm=25.0,
      tips_on_head=True,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden(
      "aspirate_task.aspirate_full_disposable_with_pre_post_tip_touch", result
    )

  async def test_partial_block_with_liquid_class_and_swirl(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    mode = normalize_head_mode("96_d_70", "single_barrel", "front_left")
    liquid_class = {
      "aspirate": {
        "w_velocity_ul_s": 25.0,
        "w_acceleration_ul_s2": 250.0,
        "z_in_velocity_mm_s": 10.0,
        "z_in_acceleration_mm_s2": 100.0,
        "z_out_velocity_mm_s": 15.0,
        "z_out_acceleration_mm_s2": 150.0,
        "post_delay_ms": 0,
      },
      "equation": {"coefficients": [0.5, 1.02]},
    }
    pipette_technique = {
      "apply_on_aspirate": True,
      "z_phase": "enter",
      "radius_mm": 1.0,
      "segments": 4,
      "clockwise": True,
    }
    task = AspirateTask(
      ctrl,
      new_teachpoints(),
      5,
      volume=30.0,
      dynamic_tip_extension=0.05,
      head_type="96_d_70",
      head_mode=mode,
      plate_selection=PlateSelection(location=5, row=1, col=2),
      labware=_plate(),
      liquid_class=liquid_class,
      pipette_technique=pipette_technique,
      teach_tip_length_mm=26.1,
      attached_tip_length_mm=26.1,
      tips_on_head=True,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden(
      "aspirate_task.aspirate_partial_block_with_liquid_class_and_swirl", result
    )

  async def test_blocked_by_neighbor_clearance(self):
    # A wide neighboring labware at location 6 overlaps the full-head
    # footprint at location 5; its height sits between what the correct
    # 2mm neighbor-clearance safety margin allows and what a smaller
    # margin would allow, so the move is blocked.
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_f_50")
    deck = DeckState()
    deck.add(5, Labware(id="target", name="target plate", height=14.5, width=85.5, length=127.5))
    deck.add(
      6,
      Labware(
        id="wide",
        name="wide reservoir",
        height=38.0,
        width=110.0,
        length=290.0,
        metadata={"length_mm": 290.0, "width_mm": 110.0, "offset_x_mm": 45.0, "offset_y_mm": 0.0},
      ),
    )
    task = AspirateTask(
      ctrl,
      new_teachpoints(),
      5,
      volume=20.0,
      head_type="96_f_50",
      attached_tip_length_mm=25.0,
      deck=deck,
    )
    result = await run_task(task, ctrl, choice_fn=lambda t: ErrorAction.ABORT)
    self.assert_matches_golden("aspirate_task.aspirate_blocked_by_neighbor_clearance", result)

  async def test_headtype_fallback_probe(self):
    # head_type=None on the task, but the controller's own tracked head
    # type is 384_d_70 (not 96_d_70), with a subset_config ("back_right")
    # whose offset genuinely differs by head geometry between the two.
    # This pins that _well_xy's XY offset always resolves against 96_d_70
    # when the task omits head_type, distinct from _effective_head_type()
    # (used for Z geometry), which reads the controller's real head type.
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("384_d_70")
    mode = normalize_head_mode("384_d_70", "single_barrel", "back_right")
    task = AspirateTask(
      ctrl,
      new_teachpoints(),
      5,
      volume=20.0,
      head_type=None,
      head_mode=mode,
      plate_selection=PlateSelection(location=5, row=1, col=1),
      labware=_plate(),
      teach_tip_length_mm=19.9,
      attached_tip_length_mm=19.9,
      tips_on_head=True,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("aspirate_task.aspirate_headtype_fallback_probe", result)


class DispenseTaskGoldenTests(GoldenFrameTestCase):
  async def test_simple(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_f_50")
    task = DispenseTask(ctrl, new_teachpoints(), 3, volume=50.0, head_type="96_f_50")
    result = await run_task(task, ctrl)
    self.assert_matches_golden("dispense_task.dispense_simple", result)

  async def test_empty_tips(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_f_50")
    task = DispenseTask(
      ctrl, new_teachpoints(), 3, volume=50.0, empty_tips=True, head_type="96_f_50"
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("dispense_task.dispense_empty_tips", result)

  async def test_dynamic_retraction_and_blowout_partial_block(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    mode = normalize_head_mode("96_d_70", "single_barrel", "back_right")
    liquid_class = {
      "dispense": {
        "w_velocity_ul_s": 20.0,
        "w_acceleration_ul_s2": 200.0,
        "z_in_velocity_mm_s": 8.0,
        "z_in_acceleration_mm_s2": 80.0,
      },
      "equation": {
        "control_points": [
          {"desired_ul": 0.0, "commanded_ul": 0.0},
          {"desired_ul": 50.0, "commanded_ul": 52.0},
          {"desired_ul": 100.0, "commanded_ul": 103.5},
        ]
      },
    }
    task = DispenseTask(
      ctrl,
      new_teachpoints(),
      5,
      volume=40.0,
      blowout_volume=5.0,
      dynamic_tip_retraction=0.02,
      tip_touch=True,
      head_type="96_d_70",
      head_mode=mode,
      plate_selection=PlateSelection(location=5, row=2, col=3),
      labware=_plate(),
      liquid_class=liquid_class,
      teach_tip_length_mm=26.1,
      attached_tip_length_mm=26.1,
      tips_on_head=True,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden(
      "dispense_task.dispense_with_dynamic_retraction_and_blowout_partial_block", result
    )


class MixTaskGoldenTests(GoldenFrameTestCase):
  async def test_basic_same_distance(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_f_50")
    task = MixTask(ctrl, new_teachpoints(), 3, volume=30.0, mix_cycles=2, head_type="96_f_50")
    result = await run_task(task, ctrl)
    self.assert_matches_golden("mix_task.mix_basic_same_distance", result)

  async def test_different_dispense_distance(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = MixTask(
      ctrl,
      new_teachpoints(),
      5,
      volume=25.0,
      pre_aspirate_volume=2.0,
      blowout_volume=1.0,
      mix_cycles=3,
      aspirate_distance=1.0,
      dispense_distance=4.0,
      dispense_at_different_distance=True,
      dynamic_tip_extension=0.03,
      tip_touch=True,
      head_type="96_d_70",
      head_mode=mode,
      plate_selection=PlateSelection(location=5, row=0, col=0),
      labware=_plate(),
      teach_tip_length_mm=26.1,
      attached_tip_length_mm=26.5,
      tips_on_head=True,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("mix_task.mix_different_dispense_distance", result)


if __name__ == "__main__":
  unittest.main()
