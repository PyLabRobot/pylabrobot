import unittest

from pylabrobot.agilent.bravo.config import (
  BravoMachineConfig,
  GripperConfig,
  HeadConfig,
  SafetyConfig,
)
from pylabrobot.agilent.bravo.types import ALL_AXES


class HeadConfigTests(unittest.TestCase):
  def test_defaults_match_source_profile(self):
    cfg = HeadConfig()
    self.assertEqual(cfg.head_type, "96_d_70")
    self.assertTrue(cfg.check_on_init)
    self.assertEqual(cfg.default_tip_capacity, 200.0)
    self.assertEqual(cfg.teach_tip_capacity, 200.0)
    self.assertIsNone(cfg.default_tip_id)
    self.assertIsNone(cfg.teach_tip_id)
    self.assertIsNone(cfg.teach_tip_length_mm)


class GripperConfigTests(unittest.TestCase):
  def test_defaults_match_source_profile(self):
    cfg = GripperConfig()
    self.assertEqual(cfg.grip_current, 0.5)
    self.assertEqual(cfg.lid_grip_current, 0.3)
    self.assertEqual(cfg.y_offset, 0.0)
    self.assertEqual(cfg.gripper_position, 5.0)
    self.assertEqual(cfg.pad_zg_reference_mm, 7.0)
    self.assertEqual(cfg.pad_reference_tip_length_mm, 26.1)


class SafetyConfigTests(unittest.TestCase):
  def test_defaults_match_source_profile(self):
    cfg = SafetyConfig()
    self.assertFalse(cfg.ignore_plate_sensor)
    self.assertFalse(cfg.ignore_w_axis)
    self.assertFalse(cfg.simulation_mode)
    self.assertEqual(cfg.z_safe_position, 0.0)
    self.assertEqual(cfg.approach_height, 10.0)
    self.assertTrue(cfg.always_move_to_safe_z)
    self.assertTrue(cfg.prompt_home_w)
    self.assertFalse(cfg.run_medium_speed)
    self.assertTrue(cfg.enable_tips_off_tip_touch)
    self.assertFalse(cfg.is_srt)
    self.assertEqual(cfg.tips_off_w_position, -11.0)
    self.assertEqual(cfg.tips_off_z_offset, 10.0)
    self.assertEqual(cfg.tips_off_tip_touch_distance, 314.96)
    self.assertEqual(cfg.head_tolerance, 25)
    self.assertEqual(cfg.safe_location, 5)
    self.assertTrue(cfg.prevent_bravo_during_robotic_access)
    self.assertFalse(cfg.allow_tos_fluid_handling)
    self.assertFalse(cfg.enable_tips_on_tip_touch)
    self.assertEqual(cfg.pin_tool_tip_type, "33 mm")

  def test_millisecond_fields_are_converted_to_seconds(self):
    # Source profile.py: tip_press_dwell_time: int = 0 (milliseconds).
    cfg = SafetyConfig()
    self.assertEqual(cfg.tip_press_dwell, 0.0)
    self.assertIsInstance(cfg.tip_press_dwell, float)

  def test_plate_sensor_transient_defaults_to_300ms_in_seconds(self):
    # Source profile.py: plate_sensor_transient_ms: int = 300.
    cfg = SafetyConfig()
    self.assertEqual(cfg.plate_sensor_transient, 0.3)


class BravoMachineConfigTests(unittest.TestCase):
  def test_default_construction(self):
    config = BravoMachineConfig()
    self.assertIsInstance(config.head, HeadConfig)
    self.assertIsInstance(config.gripper, GripperConfig)
    self.assertIsInstance(config.safety, SafetyConfig)
    self.assertEqual(set(config.axes.keys()), set(ALL_AXES))

  def test_current_limits_defaults_to_none(self):
    config = BravoMachineConfig()
    self.assertIsNone(config.current_limits)

  def test_axes_are_independent_per_instance(self):
    a = BravoMachineConfig()
    b = BravoMachineConfig()
    a.axes["x"].homing_offset = 123.0
    self.assertNotEqual(a.axes["x"].homing_offset, b.axes["x"].homing_offset)

  def test_sub_configs_are_independent_per_instance(self):
    a = BravoMachineConfig()
    b = BravoMachineConfig()
    a.head.default_tip_id = "st_10ul"
    self.assertIsNone(b.head.default_tip_id)


if __name__ == "__main__":
  unittest.main()
