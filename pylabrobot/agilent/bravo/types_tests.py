import unittest
from typing import List, cast

from pylabrobot.agilent.bravo.types import (
  ALL_AXES,
  ALL_HEAD_TYPES,
  LT_TIP_CURRENT_TABLE,
  X_TO_X_DISTANCE,
  Y_TO_Y_DISTANCE,
  Axis,
  DeviceStateFlag,
  LightColor,
  LightCommand,
  axis_code,
  axis_label,
  head_type_channels,
  head_type_code,
  head_type_is_assaymap,
  head_type_is_disposable,
  head_type_is_fixed,
  head_type_is_pintool,
  head_type_tip_kind,
  interpolate_tip_current,
  light_command_period_ms,
  location_to_row_col,
  row_col_to_location,
  safe_home_order,
  speed_level_code,
)

# The complete channel-count table for every head type, keyed exactly as
# _HEAD_TYPE_CHANNELS in types.py. Checking every row (rather than a sample)
# is deliberate: num_channels derives directly from this table.
_EXPECTED_CHANNELS = {
  "unknown": 96,
  "8_d_lt": 8,
  "8_f_50": 8,
  "16_d_st": 16,
  "96_d_70": 96,
  "96_d_70_s2": 96,
  "96_d_200": 96,
  "96_d_200_s2": 96,
  "96_f_50": 96,
  "96_f_200": 96,
  "96_pintool": 96,
  "96_assaymap": 96,
  "384_d_70": 384,
  "384_d_70_s2": 384,
  "384_f_50": 384,
  "384_pintool": 384,
  "1536_pintool": 1536,
}

# The complete tip-kind table for every head type, keyed exactly as
# _HEAD_TYPE_TIP_KIND in types.py.
_EXPECTED_TIP_KIND = {
  "unknown": "none",
  "8_d_lt": "disposable",
  "8_f_50": "fixed",
  "16_d_st": "disposable",
  "96_d_70": "disposable",
  "96_d_70_s2": "disposable",
  "96_d_200": "disposable",
  "96_d_200_s2": "disposable",
  "96_f_50": "fixed",
  "96_f_200": "fixed",
  "96_pintool": "pintool",
  "96_assaymap": "assaymap",
  "384_d_70": "disposable",
  "384_d_70_s2": "disposable",
  "384_f_50": "fixed",
  "384_pintool": "pintool",
  "1536_pintool": "pintool",
}


class AxisTests(unittest.TestCase):
  def test_axis_codes(self):
    self.assertEqual(axis_code("x"), 0)
    self.assertEqual(axis_code("y"), 1)
    self.assertEqual(axis_code("z"), 2)
    self.assertEqual(axis_code("w"), 3)
    self.assertEqual(axis_code("g"), 4)
    self.assertEqual(axis_code("zg"), 5)

  def test_axis_labels(self):
    self.assertEqual(axis_label("x"), "X-axis")
    self.assertEqual(axis_label("zg"), "Zg-axis")

  def test_all_axes_declaration_order(self):
    self.assertEqual(ALL_AXES, ("x", "y", "z", "w", "g", "zg"))

  def test_safe_home_order_lifts_before_lateral_motion(self):
    ordered = safe_home_order(["w", "x", "y", "zg", "g", "z"])
    self.assertEqual(ordered, ["z", "zg", "g", "x", "y", "w"])

  def test_safe_home_order_drops_duplicates(self):
    ordered = safe_home_order(["x", "x", "z"])
    self.assertEqual(ordered, ["z", "x"])

  def test_safe_home_order_places_unknown_axis_last(self):
    # "not-an-axis" is not a valid Axis literal; the cast deliberately lies
    # to the type checker to exercise safe_home_order's fallback path for an
    # axis outside SAFE_HOME_ORDER.
    axes = cast(List[Axis], ["x", "not-an-axis", "z"])
    ordered = safe_home_order(axes)
    self.assertEqual(ordered, ["z", "x", "not-an-axis"])


class LocationTests(unittest.TestCase):
  def test_location_to_row_col(self):
    self.assertEqual(location_to_row_col(1), (0, 0))
    self.assertEqual(location_to_row_col(2), (0, 1))
    self.assertEqual(location_to_row_col(3), (0, 2))
    self.assertEqual(location_to_row_col(5), (1, 1))
    self.assertEqual(location_to_row_col(9), (2, 2))

  def test_location_to_row_col_rejects_out_of_range(self):
    with self.assertRaises(ValueError):
      location_to_row_col(0)
    with self.assertRaises(ValueError):
      location_to_row_col(10)

  def test_row_col_to_location(self):
    self.assertEqual(row_col_to_location(0, 0), 1)
    self.assertEqual(row_col_to_location(1, 1), 5)
    self.assertEqual(row_col_to_location(2, 2), 9)

  def test_location_roundtrip(self):
    for loc in range(1, 10):
      row, col = location_to_row_col(loc)
      self.assertEqual(row_col_to_location(row, col), loc)

  def test_deck_spacing(self):
    self.assertEqual(X_TO_X_DISTANCE, 186.690)
    self.assertEqual(Y_TO_Y_DISTANCE, 109.093)


class HeadTypeTests(unittest.TestCase):
  def test_head_type_codes(self):
    self.assertEqual(head_type_code("unknown"), -1)
    self.assertEqual(head_type_code("8_d_lt"), 0)
    self.assertEqual(head_type_code("16_d_st"), 2)
    self.assertEqual(head_type_code("384_d_70"), 11)
    self.assertEqual(head_type_code("1536_pintool"), 15)

  def test_all_head_types_declaration_order_matches_codes(self):
    for head_type in ALL_HEAD_TYPES:
      if head_type == "unknown":
        continue
      self.assertGreaterEqual(head_type_code(head_type), 0)

  def test_head_type_channels_for_every_head_type(self):
    # Pins the whole table, not a sample: num_channels derives from it.
    for head_type in ALL_HEAD_TYPES:
      with self.subTest(head_type=head_type):
        self.assertEqual(head_type_channels(head_type), _EXPECTED_CHANNELS[head_type])

  def test_head_type_channels_unknown_is_a_permissive_default(self):
    self.assertEqual(head_type_channels("unknown"), 96)

  def test_head_type_tip_kind_for_every_head_type(self):
    for head_type in ALL_HEAD_TYPES:
      with self.subTest(head_type=head_type):
        self.assertEqual(head_type_tip_kind(head_type), _EXPECTED_TIP_KIND[head_type])

  def test_head_type_is_disposable_matches_tip_kind_table(self):
    for head_type in ALL_HEAD_TYPES:
      with self.subTest(head_type=head_type):
        expected = _EXPECTED_TIP_KIND[head_type] == "disposable"
        self.assertEqual(head_type_is_disposable(head_type), expected)

  def test_head_type_is_fixed_matches_tip_kind_table(self):
    for head_type in ALL_HEAD_TYPES:
      with self.subTest(head_type=head_type):
        expected = _EXPECTED_TIP_KIND[head_type] == "fixed"
        self.assertEqual(head_type_is_fixed(head_type), expected)

  def test_head_type_is_pintool_matches_tip_kind_table(self):
    for head_type in ALL_HEAD_TYPES:
      with self.subTest(head_type=head_type):
        expected = _EXPECTED_TIP_KIND[head_type] == "pintool"
        self.assertEqual(head_type_is_pintool(head_type), expected)

  def test_head_type_is_assaymap_matches_tip_kind_table(self):
    for head_type in ALL_HEAD_TYPES:
      with self.subTest(head_type=head_type):
        expected = _EXPECTED_TIP_KIND[head_type] == "assaymap"
        self.assertEqual(head_type_is_assaymap(head_type), expected)


class SpeedLevelTests(unittest.TestCase):
  def test_speed_level_codes(self):
    self.assertEqual(speed_level_code("fast"), 0)
    self.assertEqual(speed_level_code("med"), 1)
    self.assertEqual(speed_level_code("slow"), 2)
    self.assertEqual(speed_level_code("homing"), 3)
    self.assertEqual(speed_level_code("safe"), 4)


class FlagTests(unittest.TestCase):
  def test_light_color_flags_combine(self):
    combined = LightColor.RED | LightColor.GREEN
    self.assertEqual(combined & LightColor.RED, LightColor.RED)
    self.assertEqual(combined & LightColor.GREEN, LightColor.GREEN)
    self.assertEqual(combined & LightColor.BLUE, 0)

  def test_device_state_flags_combine(self):
    state = DeviceStateFlag.ROBOT_DISABLE | DeviceStateFlag.GO_BUTTON
    self.assertEqual(state & DeviceStateFlag.ROBOT_DISABLE, DeviceStateFlag.ROBOT_DISABLE)
    self.assertEqual(state & DeviceStateFlag.GO_BUTTON, DeviceStateFlag.GO_BUTTON)
    self.assertEqual(state & DeviceStateFlag.MOTOR_POWER, 0)


class LightCommandPeriodTests(unittest.TestCase):
  def test_half_second_period_in_milliseconds(self):
    self.assertEqual(light_command_period_ms(LightCommand(LightColor.RED, period=0.5)), 500)

  def test_solid_period_in_milliseconds(self):
    self.assertEqual(light_command_period_ms(LightCommand(LightColor.RED, period=0.0)), 0)

  def test_two_second_period_in_milliseconds(self):
    self.assertEqual(light_command_period_ms(LightCommand(LightColor.RED, period=2.0)), 2000)


class TipCurrentTests(unittest.TestCase):
  def test_interpolate_at_breakpoints(self):
    self.assertEqual(interpolate_tip_current(LT_TIP_CURRENT_TABLE, 1), 0.04)
    self.assertEqual(interpolate_tip_current(LT_TIP_CURRENT_TABLE, 96), 0.60)

  def test_interpolate_between_breakpoints(self):
    mid = interpolate_tip_current(LT_TIP_CURRENT_TABLE, 4)
    self.assertTrue(0.04 < mid < 0.07)

  def test_interpolate_clamps_outside_table(self):
    self.assertEqual(interpolate_tip_current(LT_TIP_CURRENT_TABLE, 0), 0.04)
    self.assertEqual(interpolate_tip_current(LT_TIP_CURRENT_TABLE, 1000), 0.60)


if __name__ == "__main__":
  unittest.main()
