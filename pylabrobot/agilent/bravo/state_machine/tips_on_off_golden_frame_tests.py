"""Golden-frame tests for TipsOnTask and TipsOffTask.

See :mod:`.golden_frame_support` for the recorder, task/engine driver, and
fixture-comparison base class this module reuses.
"""

from __future__ import annotations

import unittest

from ..deck.labware import Labware
from ..head_mode import TipSelection, normalize_head_mode
from ..types import HeadType
from .engine import ErrorAction
from .golden_frame_support import (
  GoldenFrameTestCase,
  new_config,
  new_controller,
  new_teachpoints,
  run_task,
)
from .tasks import TipsOffTask, TipsOnTask


def _tipbox_96() -> Labware:
  return Labware(
    id="lw-tipbox96",
    name="Test 96 Tip Box",
    height=60.0,
    width=85.5,
    length=127.5,
    wells=96,
    metadata={"rows": 8, "cols": 12, "spacing_x_mm": 9.0, "spacing_y_mm": 9.0},
  )


def _config_for(head_type: HeadType, teach_tip_length_mm: float = 26.1):
  config = new_config(gripper=True)
  config.head.head_type = head_type
  config.head.teach_tip_length_mm = teach_tip_length_mm
  return config


class TipsOnTaskGoldenTests(GoldenFrameTestCase):
  async def test_basic_full_head(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    config = _config_for("96_d_70")
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = TipsOnTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      TipSelection(location=3, row=0, col=0),
      3,
      tip_length_mm=26.1,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("tips_on_task.tips_on_basic_full_head", result)

  async def test_partial_block_with_w_reset(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("16_d_st")
    ctrl._axes["w"].position = 5.0
    config = _config_for("16_d_st")
    mode = normalize_head_mode("16_d_st", "single_barrel", "back_right")
    task = TipsOnTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      TipSelection(location=3, row=2, col=3),
      3,
      tip_length_mm=19.9,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("tips_on_task.tips_on_partial_block_with_w_reset", result)

  async def test_partial_block_nonzero_head_offset(self):
    # Anchored back_right on a multi-column head (96_d_70), so the
    # head-mode XY offset is genuinely nonzero -- distinct from the
    # single-column 16_d_st scenario above, whose head offset is always
    # (0, 0) regardless of anchor corner.
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    config = _config_for("96_d_70")
    mode = normalize_head_mode("96_d_70", "single_barrel", "back_right")
    task = TipsOnTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      TipSelection(location=3, row=3, col=5),
      3,
      tip_length_mm=26.1,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("tips_on_task.tips_on_partial_block_nonzero_head_offset", result)

  async def test_lt_head_press_failure(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_200")
    config = _config_for("96_d_200")
    mode = normalize_head_mode("96_d_200", "all_barrels", None)
    task = TipsOnTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      TipSelection(location=3, row=0, col=0),
      3,
      tip_length_mm=55.2,
    )

    original_jog = ctrl.jog

    def failing_jog(params):
      ctrl._record("jog", params=params)
      raise RuntimeError("Unable to reach destination on Z within tolerance.")

    ctrl.jog = failing_jog  # type: ignore[method-assign]
    try:
      result = await run_task(task, ctrl, choice_fn=lambda t: ErrorAction.ABORT)
    finally:
      ctrl.jog = original_jog  # type: ignore[method-assign]
    self.assert_matches_golden("tips_on_task.tips_on_lt_head_press_failure", result)


class TipsOffTaskGoldenTests(GoldenFrameTestCase):
  async def test_basic(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    config = _config_for("96_d_70")
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = TipsOffTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      TipSelection(location=3, row=0, col=0),
      3,
      attached_tip_length_mm=26.1,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("tips_off_task.tips_off_basic", result)

  async def test_no_tip_touch_trash(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    config = _config_for("96_d_70")
    config.safety.enable_tips_off_tip_touch = False
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = TipsOffTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      None,
      3,
      attached_tip_length_mm=26.1,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("tips_off_task.tips_off_no_tip_touch_trash", result)

  async def test_not_tracked_prompt(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    config = _config_for("96_d_70")
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = TipsOffTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      TipSelection(location=3, row=0, col=0),
      3,
      attached_tip_length_mm=26.1,
      tips_are_tracked=False,
    )
    result = await run_task(task, ctrl, choice_fn=lambda t: ErrorAction.ABORT)
    self.assert_matches_golden("tips_off_task.tips_off_not_tracked_prompt", result)

  async def test_partial_block_anchor(self):
    ctrl = new_controller(all_homed=True, gripper=True)
    ctrl.set_head_type("96_d_70")
    config = _config_for("96_d_70")
    mode = normalize_head_mode("96_d_70", "single_barrel", "back_right")
    task = TipsOffTask(
      ctrl,
      new_teachpoints(),
      config,
      _tipbox_96(),
      mode,
      TipSelection(location=3, row=1, col=2),
      3,
      attached_tip_length_mm=26.1,
    )
    result = await run_task(task, ctrl)
    self.assert_matches_golden("tips_off_task.tips_off_partial_block_anchor", result)


if __name__ == "__main__":
  unittest.main()
