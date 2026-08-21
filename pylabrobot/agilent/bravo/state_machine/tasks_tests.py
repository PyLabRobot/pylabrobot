"""Unit tests for module-level helpers in :mod:`.tasks`.

Motion-sequencing behavior is covered by the golden-frame test modules in
this package; this module is for helpers whose contract is best pinned
directly rather than through a full task run.
"""

from __future__ import annotations

import asyncio
import unittest

from ..config import BravoMachineConfig
from ..controllers.agile_7612 import Agile7612Controller
from ..controllers.simulation import SimulationController
from ..darwin.controller import DarwinController
from ..darwin.darwin_golden_frame_tests import FakeGeminiTransport
from ..deck.labware import DeckState, Labware
from ..deck.teachpoints import Teachpoints
from ..head_mode import TipSelection, normalize_head_mode
from ..protocol.v11_comm_tests import BufferedTransport
from .tasks import (
  AspirateTask,
  PickPlaceTask,
  TipsOffTask,
  TipsOnTask,
  _axis_move,
  _infer_stack_count_from_scan_height,
  _stack_total_height_for_count,
  _stacking_support_height_for_count,
  _w_axis_motion_value,
)


class AxisMovePositionIsNeverConvertedTests(unittest.TestCase):
  """_axis_move cannot tell a volume from a park/offset position by looking
  at a bare float, so it never converts ``position`` for any axis --
  including W. A caller that has a genuine volume converts it itself,
  before combining it with a controller-native quantity (see
  AspirateVolumeConversionTests below); a caller with a millimetre value
  (a park position, a teachpoint, an offset-table entry) passes it straight
  through.
  """

  def test_w_position_passes_through_unconverted_on_agile(self):
    ctrl = SimulationController(head_type="96_d_70")
    move = _axis_move(ctrl, "w", 50.0)
    self.assertEqual(move.position, 50.0)

  def test_w_position_passes_through_unconverted_on_darwin(self):
    ctrl = DarwinController(FakeGeminiTransport())
    ctrl.set_head_type("96_d_70")
    move = _axis_move(ctrl, "w", -11.0)
    self.assertEqual(move.position, -11.0)

  def test_non_w_position_is_also_never_converted(self):
    ctrl = DarwinController(FakeGeminiTransport())
    ctrl.set_head_type("96_d_70")
    move = _axis_move(ctrl, "z", 50.0, velocity=25.0, acceleration=250.0)
    self.assertEqual(move.position, 50.0)


class AxisMoveWVelocityConversionTests(unittest.TestCase):
  """Unlike position, a W-axis velocity/acceleration passed to _axis_move
  is always a volume rate (every caller supplies it from a liquid class's
  w_velocity_ul_s/w_acceleration_ul_s2 entries), so converting it here is
  unambiguous and correct.
  """

  def test_w_velocity_and_acceleration_are_unconverted_on_agile(self):
    ctrl = SimulationController(head_type="96_d_70")
    move = _axis_move(ctrl, "w", 50.0, velocity=25.0, acceleration=250.0)
    self.assertEqual(move.velocity, 25.0)
    self.assertEqual(move.acceleration, 250.0)

  def test_w_velocity_and_acceleration_are_converted_on_darwin(self):
    ctrl = DarwinController(FakeGeminiTransport())
    ctrl.set_head_type("96_d_70")
    move = _axis_move(ctrl, "w", 50.0, velocity=25.0, acceleration=250.0)
    self.assertNotEqual(move.velocity, 25.0)
    self.assertNotEqual(move.acceleration, 250.0)
    self.assertEqual(move.velocity, ctrl.ul_to_mm(25.0))
    self.assertEqual(move.acceleration, ctrl.ul_to_mm(250.0))

  def test_non_w_axis_velocity_and_acceleration_are_never_converted(self):
    ctrl = DarwinController(FakeGeminiTransport())
    ctrl.set_head_type("96_d_70")
    move = _axis_move(ctrl, "z", 50.0, velocity=25.0, acceleration=250.0)
    self.assertEqual(move.velocity, 25.0)
    self.assertEqual(move.acceleration, 250.0)


class WAxisMotionValueTests(unittest.TestCase):
  """_w_axis_motion_value is the one place a caller-known volume is
  converted to a controller's native W unit -- callers combine its result
  with a controller-native quantity (e.g. a current W position) themselves,
  rather than handing an already-combined value back through _axis_move.
  """

  def test_matches_the_head_specific_ul_to_mm_factor(self):
    # Hardcoded expected values, independent of ul_to_mm() itself.
    darwin = DarwinController(FakeGeminiTransport())
    darwin.set_head_type("96_d_70")
    self.assertAlmostEqual(_w_axis_motion_value(darwin, 50.0), 11.2, places=6)

    darwin.set_head_type("384_d_70")
    self.assertAlmostEqual(_w_axis_motion_value(darwin, 50.0), 42.3, places=6)

    agile = SimulationController(head_type="96_d_70")
    self.assertEqual(_w_axis_motion_value(agile, 50.0), 50.0)

  def test_falls_back_to_unconverted_when_conversion_fails(self):
    # No head type has been set on this Darwin controller (still "unknown"),
    # so ul_to_mm() has nothing to convert against and raises.
    ctrl = DarwinController(FakeGeminiTransport())
    self.assertEqual(_w_axis_motion_value(ctrl, 50.0), 50.0)


class AspirateVolumeConversionTests(unittest.TestCase):
  """AspirateTask._aspirate_volume combines a controller-native current W
  position with a converted volume delta itself, then hands the combined,
  already-native result to _axis_move -- which must not convert it again.
  A regression that reintroduces position conversion inside _axis_move
  would double-convert this on Darwin (identity on Agile, so a
  SimulationController-based golden fixture cannot catch it).
  """

  def _run_aspirate_volume(self, ctrl, *, current_w: float):
    original_get_position = ctrl.get_position

    def stub_get_position(axis):
      if axis == "w":
        return current_w
      return original_get_position(axis)

    ctrl.get_position = stub_get_position  # type: ignore[method-assign]

    moves: list = []

    def recording_move(move_list, wait=True, timeout=30.0):
      moves.extend(move_list)

    ctrl.move = recording_move  # type: ignore[method-assign]

    task = AspirateTask(
      ctrl,
      _teachpoints(),
      3,
      volume=50.0,
      head_type="96_f_50",  # fixed-tip: no attached/taught tip length needed
    )
    asyncio.run(task._aspirate_volume())
    return [m for m in moves if m.axis == "w"][0]

  def test_agile_combines_current_position_and_volume_directly(self):
    ctrl = SimulationController(head_type="96_d_70")
    move = self._run_aspirate_volume(ctrl, current_w=10.0)
    self.assertEqual(move.position, 10.0 + 50.0)

  def test_darwin_combines_current_position_and_converted_volume_once(self):
    ctrl = DarwinController(FakeGeminiTransport())
    ctrl.set_head_type("96_d_70")
    current_w_mm = 5.0
    move = self._run_aspirate_volume(ctrl, current_w=current_w_mm)
    expected = current_w_mm + ctrl.ul_to_mm(50.0)
    self.assertAlmostEqual(move.position, expected, places=6)
    # A double conversion (the bug this test guards against) would apply
    # ul_to_mm to the whole sum again, which is a different, smaller number
    # for any factor other than 1.0.
    self.assertNotAlmostEqual(move.position, ctrl.ul_to_mm(expected), places=6)


def _tipbox() -> Labware:
  return Labware(
    id="lw-tipbox96",
    name="Test 96 Tip Box",
    height=60.0,
    width=85.5,
    length=127.5,
    wells=96,
    metadata={"rows": 8, "cols": 12, "spacing_x_mm": 9.0, "spacing_y_mm": 9.0},
  )


def _teachpoints() -> Teachpoints:
  teachpoints = Teachpoints()
  teachpoints.set_default_teachpoints("96_d_70")
  return teachpoints


def _plate(name: str = "lw-plate") -> Labware:
  return Labware(
    id=name,
    name=name,
    height=14.0,
    width=85.5,
    length=127.5,
    gripper_offset=2.0,
    wells=96,
    metadata={"rows": 8, "cols": 12, "spacing_x_mm": 9.0, "spacing_y_mm": 9.0},
  )


class PickPlaceAlreadyGrippedTests(unittest.TestCase):
  """plate_already_gripped seeds the state a completed grip leaves behind,
  as a supported constructor argument rather than a caller poking
  _plate_pick_verified/_grip_attempts directly.
  """

  def _deck_with_plate_at(self, location: int) -> DeckState:
    deck = DeckState()
    deck.set_single(location, _plate())
    return deck

  @staticmethod
  def _config() -> BravoMachineConfig:
    config = BravoMachineConfig()
    config.head.teach_tip_length_mm = 26.1
    return config

  def test_default_construction_is_not_gripped(self):
    task = PickPlaceTask(
      SimulationController(),
      _teachpoints(),
      self._config(),
      self._deck_with_plate_at(1),
      1,
      2,
    )
    self.assertFalse(task._plate_pick_verified)
    self.assertEqual(task._grip_attempts, 0)

  def test_plate_already_gripped_seeds_verified_state(self):
    task = PickPlaceTask(
      SimulationController(),
      _teachpoints(),
      self._config(),
      self._deck_with_plate_at(1),
      1,
      2,
      plate_already_gripped=True,
    )
    self.assertTrue(task._plate_pick_verified)
    self.assertEqual(task._grip_attempts, 1)

  def test_release_plate_moves_the_deck_group_only_when_already_gripped(self):
    """_release_plate's deck-state update (remove from source, add to
    destination) is gated on _plate_pick_verified; a task constructed
    without plate_already_gripped takes the "skipped" branch and leaves
    the deck alone, so this pins that plate_already_gripped is what makes
    the transfer happen, not merely a status flag.
    """
    deck = self._deck_with_plate_at(1)
    task = PickPlaceTask(
      SimulationController(),
      _teachpoints(),
      self._config(),
      deck,
      1,
      2,
      plate_already_gripped=True,
    )
    asyncio.run(task._release_plate())
    self.assertIsNone(deck.get_stack(1).top)
    self.assertIsNotNone(deck.get_stack(2).top)

  def test_release_plate_without_already_gripped_leaves_the_deck_alone(self):
    deck = self._deck_with_plate_at(1)
    task = PickPlaceTask(
      SimulationController(),
      _teachpoints(),
      self._config(),
      deck,
      1,
      2,
    )
    asyncio.run(task._release_plate())
    self.assertIsNotNone(deck.get_stack(1).top)
    self.assertIsNone(deck.get_stack(2).top)


class TipsOffEjectPositionIsNeverConvertedTests(unittest.TestCase):
  """TipsOffTask._eject_tips drives W to
  config.safety.tips_off_w_position -- a plunger park position in
  millimetres (SafetyConfig.tips_off_w_position defaults to -11.0, a
  negative value; no volume is ever negative), not a volume. It must reach
  the controller unconverted on every generation, Darwin included.
  """

  def _run_eject(self, ctrl):
    config = BravoMachineConfig()
    config.head.head_type = "96_d_70"
    config.head.teach_tip_length_mm = 26.1
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = TipsOffTask(
      ctrl,
      _teachpoints(),
      config,
      _tipbox(),
      mode,
      TipSelection(location=3, row=0, col=0),
      3,
      attached_tip_length_mm=26.1,
    )
    # Intercepts move() entirely rather than delegating to the real
    # implementation: this pins what _eject_tips() constructs and hands to
    # the controller, independent of what a specific backend's move() then
    # does with it (which needs a live transport/engine to exercise).
    moves: list = []
    original_move = ctrl.move

    def recording_move(move_list, wait=True, timeout=30.0):
      moves.extend(move_list)

    ctrl.move = recording_move  # type: ignore[method-assign]
    try:
      asyncio.run(task._eject_tips())
    finally:
      ctrl.move = original_move  # type: ignore[method-assign]
    return [m for m in moves if m.axis == "w"]

  def test_eject_w_target_is_unconverted_on_agile(self):
    ctrl = SimulationController(head_type="96_d_70")
    eject_move = self._run_eject(ctrl)[0]
    self.assertEqual(eject_move.position, -11.0)  # SafetyConfig.tips_off_w_position default

  def test_eject_w_target_is_also_unconverted_on_darwin(self):
    ctrl = DarwinController(FakeGeminiTransport())
    ctrl.set_head_type("96_d_70")
    eject_move = self._run_eject(ctrl)[0]
    self.assertEqual(eject_move.position, -11.0)
    self.assertNotEqual(eject_move.position, ctrl.ul_to_mm(-11.0))


class TipsOnTipForceJogRoutingTests(unittest.TestCase):
  """TipsOnTask._lower_z_to_tips presses with an Agile7612-specific
  force-jog sequence when the controller supports it, and the generic
  jog() otherwise.
  """

  def _run_lower_z(self, ctrl):
    config = BravoMachineConfig()
    config.head.head_type = "96_d_70"
    config.head.teach_tip_length_mm = 26.1
    mode = normalize_head_mode("96_d_70", "all_barrels", None)
    task = TipsOnTask(
      ctrl,
      _teachpoints(),
      config,
      _tipbox(),
      mode,
      TipSelection(location=3, row=0, col=0),
      3,
      tip_length_mm=26.1,
    )
    asyncio.run(task._lower_z_to_tips())

  def test_agile_7612_controller_uses_tip_force_jog(self):
    ctrl = Agile7612Controller(BufferedTransport())
    calls: list = []

    def recording_tip_force_jog(axis, peak_current, max_position):
      calls.append((axis, peak_current, max_position))
      return max_position

    ctrl.tip_force_jog = recording_tip_force_jog  # type: ignore[method-assign]
    self._run_lower_z(ctrl)
    self.assertEqual(len(calls), 1)
    self.assertEqual(calls[0][0], "z")

  def test_simulation_controller_uses_generic_jog(self):
    ctrl = SimulationController(head_type="96_d_70")
    calls: list = []
    original_jog = ctrl.jog

    def recording_jog(params):
      calls.append(params)
      return original_jog(params)

    ctrl.jog = recording_jog  # type: ignore[method-assign]
    self._run_lower_z(ctrl)
    self.assertEqual(len(calls), 1)
    self.assertEqual(calls[0].axis, "z")


if __name__ == "__main__":
  unittest.main()


class StackHeightArithmeticTests(unittest.TestCase):
  """Pins the stack-height/count helpers directly.

  ScanStackHeightTask's simulation-shortcut path derives its reported
  count from the deck's own plate count, not from these helpers -- they
  only feed informational height fields on the result, which a
  golden-frame comparison (scoped to the ordered controller calls a task
  issues) never inspects. A wrong increment here is invisible to every
  golden fixture in this package regardless of which scenario exercises
  it, so it needs a direct numeric pin instead.
  """

  def test_support_height_is_zero_for_zero_or_one_plate(self):
    self.assertEqual(_stacking_support_height_for_count(0, 14.5), 0.0)
    self.assertEqual(_stacking_support_height_for_count(1, 14.5), 0.0)

  def test_support_height_is_n_minus_one_times_thickness(self):
    self.assertEqual(_stacking_support_height_for_count(3, 14.5), 2 * 14.5)
    self.assertEqual(_stacking_support_height_for_count(5, 2.0), 4 * 2.0)

  def test_total_height_is_zero_for_zero_plates(self):
    self.assertEqual(_stack_total_height_for_count(0, 14.5, 14.5), 0.0)

  def test_total_height_is_top_plate_plus_support(self):
    # Top plate's own height (14.5) plus 2 supporting plates' worth of
    # stacking thickness (2 * 14.5).
    self.assertEqual(_stack_total_height_for_count(3, 14.5, 14.5), 14.5 + 2 * 14.5)

  def test_infer_count_of_a_single_plate_is_height_independent(self):
    # A single plate of any height leaves ~0 support height and always
    # resolves to 1, regardless of the plate's own height.
    self.assertEqual(_infer_stack_count_from_scan_height(14.5, 14.5, top_plate_height_mm=14.5), 1)
    self.assertEqual(_infer_stack_count_from_scan_height(60.0, 14.5, top_plate_height_mm=60.0), 1)

  def test_infer_count_rounds_to_the_nearest_plate(self):
    # 3 plates of 14.5 mm: top-of-stack height = 14.5 + 2*14.5 = 43.5.
    self.assertEqual(_infer_stack_count_from_scan_height(43.5, 14.5, top_plate_height_mm=14.5), 3)

  def test_infer_count_floors_at_one_for_zero_thickness(self):
    self.assertEqual(_infer_stack_count_from_scan_height(50.0, 0.0), 1)
