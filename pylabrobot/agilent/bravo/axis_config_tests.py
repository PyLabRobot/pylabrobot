import unittest

from pylabrobot.agilent.bravo.axis_config import DEFAULT_SPEEDS, AxisConfig, default_axis_config
from pylabrobot.agilent.bravo.types import (
  ALL_AXES,
  AXIS_RANGES,
  DEFAULT_W_TICKS_PER_UL,
  TICKS_PER_MM,
)


class DefaultAxisConfigTests(unittest.TestCase):
  def test_every_axis_gets_a_complete_default_config(self):
    for axis in ALL_AXES:
      cfg = default_axis_config(axis)
      self.assertEqual(cfg.axis, axis)
      self.assertEqual(cfg.range, AXIS_RANGES[axis])
      self.assertIsInstance(cfg.ticks_per_eng_unit, float)
      self.assertGreater(cfg.ticks_per_eng_unit, 0.0)

  def test_linear_axes_use_ticks_per_mm(self):
    for axis, ticks in TICKS_PER_MM.items():
      self.assertEqual(default_axis_config(axis).ticks_per_eng_unit, ticks)

  def test_w_axis_uses_the_shared_head_independent_default(self):
    # W has no fixed mm scale in TICKS_PER_MM (it depends on the installed
    # head), so the default config uses DEFAULT_W_TICKS_PER_UL -- the same
    # constant every controller in this package seeds itself with -- rather
    # than an arbitrary placeholder.
    self.assertNotIn("w", TICKS_PER_MM)
    self.assertEqual(default_axis_config("w").ticks_per_eng_unit, DEFAULT_W_TICKS_PER_UL)
    self.assertEqual(default_axis_config("w").ticks_per_eng_unit, 48.0)

  def test_default_speeds_present_for_every_speed_level(self):
    for axis in ALL_AXES:
      cfg = default_axis_config(axis)
      for level in ("fast", "med", "slow", "homing", "safe"):
        self.assertIn(level, cfg.speeds, f"{axis} missing speed level {level}")

  def test_scalar_defaults_match_dataclass_defaults(self):
    cfg = default_axis_config("x")
    self.assertEqual(cfg.homing_offset, 0.0)
    self.assertFalse(cfg.home_in_positive_direction)
    self.assertEqual(cfg.home_flag_bitmask, 0)
    self.assertEqual(cfg.home_complete_register, 0)
    self.assertTrue(cfg.check_for_alignment)

  def test_default_speeds_table_covers_every_axis(self):
    for axis in ALL_AXES:
      self.assertIn(axis, DEFAULT_SPEEDS)


class AxisConfigConstructionTests(unittest.TestCase):
  def test_explicit_config_overrides_every_field(self):
    cfg = AxisConfig(
      axis="zg",
      ticks_per_eng_unit=787.4,
      range=AXIS_RANGES["zg"],
      homing_offset=-20.0,
      home_in_positive_direction=True,
      home_flag_bitmask=0x02,
      home_flag_register=0x10,
      home_complete_register=0x5F,
      homing_soft_stop_decel=150.0,
      min_move_full_accel=5.0,
      check_for_alignment=False,
      speeds={},
    )
    self.assertEqual(cfg.homing_offset, -20.0)
    self.assertTrue(cfg.home_in_positive_direction)
    self.assertEqual(cfg.home_flag_bitmask, 0x02)
    self.assertEqual(cfg.home_complete_register, 0x5F)
    self.assertFalse(cfg.check_for_alignment)


if __name__ == "__main__":
  unittest.main()
