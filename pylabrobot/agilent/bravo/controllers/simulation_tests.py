import unittest

from pylabrobot.agilent.bravo.controllers.base import AxisMoveInfo
from pylabrobot.agilent.bravo.controllers.simulation import SimulationController
from pylabrobot.agilent.bravo.types import (
  ALL_AXES,
  GripperDetectionState,
  HeadType,
  head_type_code,
)


class ConnectionWithoutTransportTests(unittest.TestCase):
  def test_reports_connected_immediately_with_no_transport_argument(self):
    # SimulationController() takes no transport at all.
    controller = SimulationController()
    self.assertTrue(controller.is_connected)
    self.assertTrue(controller.ping())


class HomingTests(unittest.TestCase):
  def test_axes_start_homed_at_zero_with_no_configured_offsets(self):
    controller = SimulationController()
    for axis in ALL_AXES:
      self.assertTrue(controller.is_axis_homed(axis), f"{axis} should start homed")
      self.assertEqual(controller.get_position(axis), 0.0)

  def test_homing_offsets_set_the_initial_homed_position(self):
    controller = SimulationController(homing_offsets={"x": 12.3, "zg": -4.0})
    self.assertTrue(controller.is_axis_homed("x"))
    self.assertEqual(controller.get_position("x"), 12.3)
    self.assertEqual(controller.get_position("zg"), -4.0)
    # An axis with no entry in homing_offsets still starts homed, at 0.0.
    self.assertTrue(controller.is_axis_homed("y"))
    self.assertEqual(controller.get_position("y"), 0.0)

  def test_home_axes_returns_a_moved_axis_to_its_offset(self):
    controller = SimulationController(homing_offsets={"z": 8.0})
    controller.move([AxisMoveInfo(axis="z", position=42.0)])
    self.assertNotEqual(controller.get_position("z"), 8.0)
    controller.home_axes(["z"])
    self.assertEqual(controller.get_position("z"), 8.0)
    self.assertEqual(controller.get_position("z"), controller.get_park_position("z"))

  def test_home_axes_only_resets_the_requested_axis(self):
    controller = SimulationController()
    controller.move([AxisMoveInfo(axis="x", position=50.0)])
    controller.move([AxisMoveInfo(axis="y", position=50.0)])
    controller.home_axes(["x"])
    self.assertEqual(controller.get_position("x"), 0.0)
    # A mutation that homes every axis regardless of the argument would pass
    # a test that only checks "x", so an untouched axis is checked too.
    self.assertEqual(controller.get_position("y"), 50.0)


class MoveTests(unittest.TestCase):
  def test_absolute_move_updates_reported_position(self):
    controller = SimulationController()
    controller.move([AxisMoveInfo(axis="x", position=55.0, absolute=True)])
    self.assertEqual(controller.get_position("x"), 55.0)

  def test_absolute_move_replaces_rather_than_adds_to_current_position(self):
    # Starting from a nonzero position distinguishes an absolute move (lands
    # exactly on the target) from a relative move (would land on the sum).
    controller = SimulationController()
    controller.move([AxisMoveInfo(axis="x", position=20.0, absolute=True)])
    controller.move([AxisMoveInfo(axis="x", position=55.0, absolute=True)])
    self.assertEqual(controller.get_position("x"), 55.0)

  def test_relative_move_adds_to_current_position(self):
    controller = SimulationController()
    controller.move([AxisMoveInfo(axis="x", position=10.0, absolute=True)])
    controller.move([AxisMoveInfo(axis="x", position=5.0, absolute=False)])
    self.assertEqual(controller.get_position("x"), 15.0)

  def test_move_only_changes_the_targeted_axis(self):
    controller = SimulationController()
    controller.move([AxisMoveInfo(axis="x", position=99.0)])
    self.assertEqual(controller.get_position("y"), 0.0)


class InitializeTests(unittest.TestCase):
  def test_initialize_is_idempotent_immediately_after_construction(self):
    # Every axis already starts homed at its offset, so initialize() right
    # after construction should be a no-op, not a state transition.
    controller = SimulationController(homing_offsets={"x": 7.5, "g": 2.0})
    positions_before = controller.get_all_positions()
    homed_before = controller.get_all_homed()
    controller.initialize()
    self.assertEqual(controller.get_all_positions(), positions_before)
    self.assertEqual(controller.get_all_homed(), homed_before)

  def test_initialize_rehomes_an_axis_that_has_moved(self):
    controller = SimulationController(homing_offsets={"x": 7.5})
    controller.move([AxisMoveInfo(axis="x", position=99.0)])
    self.assertNotEqual(controller.get_position("x"), 7.5)
    controller.initialize()
    self.assertEqual(controller.get_position("x"), 7.5)
    self.assertTrue(controller.is_axis_homed("x"))

  def test_initialize_leaves_controller_connected_and_pingable(self):
    controller = SimulationController()
    controller.initialize()
    self.assertTrue(controller.is_connected)
    self.assertTrue(controller.ping())


class HeadTypeTests(unittest.TestCase):
  def test_default_head_type_is_96_d_70(self):
    controller = SimulationController()
    self.assertEqual(controller.read_smart_head_type(), head_type_code("96_d_70"))

  def test_constructor_head_type_is_reflected_in_smart_head_type(self):
    controller = SimulationController(head_type="384_d_70")
    self.assertEqual(controller.read_smart_head_type(), head_type_code("384_d_70"))
    self.assertNotEqual(controller.read_smart_head_type(), head_type_code("96_d_70"))

  def test_read_head_adc_matches_the_known_table_value(self):
    expected: dict[HeadType, int] = {
      "96_d_70": 2745,
      "96_d_200": 2600,
      "384_d_70": 2400,
      "96_f_50": 2200,
      "8_d_lt": 2000,
    }
    for head_type, adc_value in expected.items():
      controller = SimulationController(head_type=head_type)
      self.assertEqual(controller.read_head_adc(), adc_value, head_type)

  def test_read_head_adc_falls_back_to_the_96_d_70_value_for_an_unknown_head_type(self):
    # 1536_pintool has no entry in the ADC table; the source's own fallback
    # is the 96_d_70 reading, not a guess specific to the unlisted head.
    controller = SimulationController(head_type="1536_pintool")
    self.assertEqual(controller.read_head_adc(), 2745)

  def test_set_head_type_changes_reported_head_type(self):
    controller = SimulationController(head_type="96_d_70")
    controller.set_head_type("16_d_st")
    self.assertEqual(controller.read_smart_head_type(), head_type_code("16_d_st"))

  def test_detect_smart_head_reports_true(self):
    controller = SimulationController()
    self.assertTrue(controller.detect_smart_head())


class MotorControlTests(unittest.TestCase):
  def test_motor_starts_disabled_then_can_be_enabled_and_disabled(self):
    controller = SimulationController()
    self.assertFalse(controller.is_motor_enabled("x"))
    controller.enable_motor("x")
    self.assertTrue(controller.is_motor_enabled("x"))
    controller.disable_motor("x")
    self.assertFalse(controller.is_motor_enabled("x"))


class GripperTests(unittest.TestCase):
  def test_grip_moves_g_axis_and_reports_plate_present(self):
    controller = SimulationController()
    self.assertFalse(controller.is_plate_in_gripper())
    controller.grip(speed="slow", position=3.2)
    self.assertTrue(controller.is_plate_in_gripper())
    self.assertEqual(controller.get_position("g"), 3.2)

  def test_open_gripper_clears_plate_present_and_resets_position(self):
    controller = SimulationController()
    controller.grip(speed="slow", position=3.2)
    controller.open_gripper()
    self.assertFalse(controller.is_plate_in_gripper())
    self.assertEqual(controller.get_position("g"), 0.0)

  def test_open_gripper_accepts_explicit_position(self):
    controller = SimulationController()
    controller.open_gripper(position=-1.5)
    self.assertEqual(controller.get_position("g"), -1.5)

  def test_detect_gripper_reflects_configured_state(self):
    controller = SimulationController()
    controller.set_gripper_state(GripperDetectionState.NOT_DETECTED)
    self.assertEqual(controller.detect_gripper(), GripperDetectionState.NOT_DETECTED)


class PlateSensorAndScanTests(unittest.TestCase):
  def test_read_plate_sensor_reflects_configured_state(self):
    controller = SimulationController()
    self.assertFalse(controller.read_plate_sensor())
    controller.set_plate_sensor_present(True)
    self.assertTrue(controller.read_plate_sensor())

  def test_scan_without_configured_height_reports_not_detected(self):
    controller = SimulationController()
    result = controller.scan_stack_with_gripper(start_zg=0.0, end_zg=50.0, speed="slow")
    self.assertFalse(result["detected"])
    self.assertEqual(result["final_zg"], 50.0)
    self.assertEqual(controller.get_position("zg"), 50.0)

  def test_scan_with_configured_height_reports_detected_position(self):
    controller = SimulationController()
    controller.set_simulated_scan_height_mm(12.0)
    result = controller.scan_stack_with_gripper(start_zg=0.0, end_zg=50.0, speed="slow")
    self.assertTrue(result["detected"])
    self.assertEqual(result["final_zg"], 12.0)
    self.assertEqual(controller.get_position("zg"), 12.0)


class DeviceStateTests(unittest.TestCase):
  def test_go_button_can_be_set_and_cleared(self):
    controller = SimulationController()
    self.assertFalse(controller.is_go_button_pressed())
    controller.set_go_button(True)
    self.assertTrue(controller.is_go_button_pressed())
    controller.clear_go_button()
    self.assertFalse(controller.is_go_button_pressed())

  def test_last_error_starts_none(self):
    controller = SimulationController()
    self.assertIsNone(controller.last_error)


if __name__ == "__main__":
  unittest.main()
