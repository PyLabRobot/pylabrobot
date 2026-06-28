"""Typed, immutable view of a TundraStore's on-device settings.

The device exposes its full calibration/configuration via the ``settings``
command as ``NAME = value`` text. Each key is surfaced here as one explicitly
typed attribute (the device ``NAME`` lower-cased); types are inferred from the
device's own values. A :class:`TundraStoreSettings` is loaded whole from the
device (or a capture) and is frozen once built.
"""

import warnings
from dataclasses import dataclass, fields
from typing import Dict, Iterable, Tuple

try:
  from typing import Literal
except ImportError:  # pragma: no cover
  from typing_extensions import Literal  # type: ignore


# Known TundraStore/SteriStore models, as reported by the device's MACHINE_TYPE
# setting. Extend this (and MachineType) when a new model is encountered.
MachineType = Literal["SteriStore2"]
KNOWN_MACHINE_TYPES: Tuple[str, ...] = ("SteriStore2",)


@dataclass(frozen=True)
class TundraStoreSettings:
  """All on-device settings, one typed attribute per device key."""

  product_name: str
  product_description: str

  serial_number: str

  machine_type: MachineType

  rest_server_port: int

  syslog_server: str
  syslog_level: int

  internal_log_level: int

  carousel_home_speed_fast: float
  carousel_home_speed_slow: float
  carousel_home_acceleration: float
  carousel_velocity: float
  carousel_idle_velocity: float
  carousel_acceleration: float
  carousel_abort_deceleration: float
  carousel_jerk: float
  carousel_final_drive_jerk: float
  carousel_stacker_0_pos: float
  carousel_stacker_1_pos: float
  carousel_stacker_2_pos: float
  carousel_stacker_count: int
  carousel_count: int
  carousel_calibration_offset: float

  spatula_home_speed_fast: float
  spatula_home_speed_slow: float
  spatula_home_acceleration: float
  spatula_velocity: float
  spatula_velocity_with_plate: float
  spatula_acceleration: float
  spatula_abort_deceleration: float
  spatula_jerk: float
  spatula_rot_home_speed_fast: float
  spatula_rot_home_speed_slow: float
  spatula_rot_home_acceleration: float
  spatula_rot_velocity: float
  spatula_rot_acceleration: float
  spatula_rot_abort_deceleration: float
  spatula_rot_jerk: float
  spatula_rot_zero_pos: float
  spatula_rot_stack_pos_0: float
  spatula_rot_stack_pos_1: float
  spatula_rot_stack_pos_2: float
  spatula_rot_nest_1_pos: float
  spatula_rot_nest_2_pos: float
  spatula_rot_nest_3_pos: float
  spatula_rot_nest_4_pos: float
  spatula_rot_nest_5_pos: float
  spatula_rot_nest_6_pos: float
  spatula_rot_nest_7_pos: float
  spatula_rot_nest_8_pos: float
  spatula_rot_nest_9_pos: float
  spatula_rot_nest_10_pos: float
  spatula_rot_nest_21_pos: float
  spatula_rot_nest_22_pos: float
  spatula_rot_nest_23_pos: float
  spatula_rot_nest_24_pos: float
  spatula_rot_nest_51_pos: float
  spatula_rot_nest_52_pos: float
  spatula_rot_nest_61_pos: float
  spatula_rot_nest_62_pos: float
  spatula_rot_nest_63_pos: float
  spatula_rot_nest_64_pos: float
  spatula_rot_nest_65_pos: float
  spatula_rot_nest_66_pos: float
  spatula_rot_nest_67_pos: float
  spatula_rot_nest_68_pos: float
  spatula_rot_nest_69_pos: float
  spatula_slide_home_speed_fast: float
  spatula_slide_home_speed_slow: float
  spatula_slide_home_acceleration: float
  spatula_slide_home_offset: float
  spatula_slide_velocity: float
  spatula_slide_acceleration: float
  spatula_slide_abort_deceleration: float
  spatula_slide_jerk: float
  spatula_slide_in_pos_0: float
  spatula_slide_in_pos_1: float
  spatula_slide_in_pos_2: float
  spatula_slide_nest_1_pos: float
  spatula_slide_nest_2_pos: float
  spatula_slide_nest_3_pos: float
  spatula_slide_nest_4_pos: float
  spatula_slide_nest_5_pos: float
  spatula_slide_nest_6_pos: float
  spatula_slide_nest_7_pos: float
  spatula_slide_nest_8_pos: float
  spatula_slide_nest_9_pos: float
  spatula_slide_nest_10_pos: float
  spatula_slide_nest_21_pos: float
  spatula_slide_nest_22_pos: float
  spatula_slide_nest_23_pos: float
  spatula_slide_nest_24_pos: float
  spatula_slide_nest_51_pos: float
  spatula_slide_nest_52_pos: float
  spatula_slide_nest_61_pos: float
  spatula_slide_nest_62_pos: float
  spatula_slide_nest_63_pos: float
  spatula_slide_nest_64_pos: float
  spatula_slide_nest_65_pos: float
  spatula_slide_nest_66_pos: float
  spatula_slide_nest_67_pos: float
  spatula_slide_nest_68_pos: float
  spatula_slide_nest_69_pos: float
  spatula_valve_hold: int
  spatula_plate_sensor: int
  spatula_plate_release_sensor: int

  inner_user_door_sensor: int

  door_open_sensor_output: int

  nest_count: int
  nest_1_height: float
  nest_2_height: float
  nest_3_height: float
  nest_4_height: float
  nest_5_height: float
  nest_6_height: float
  nest_7_height: float
  nest_8_height: float
  nest_9_height: float
  nest_10_height: float
  nest_21_height: float
  nest_22_height: float
  nest_23_height: float
  nest_24_height: float
  nest_51_height: float
  nest_52_height: float
  nest_61_height: float
  nest_62_height: float
  nest_63_height: float
  nest_64_height: float
  nest_65_height: float
  nest_66_height: float
  nest_67_height: float
  nest_68_height: float
  nest_69_height: float
  nest_1_style: str
  nest_2_style: str
  nest_3_style: str
  nest_4_style: str
  nest_5_style: str
  nest_6_style: str
  nest_7_style: str
  nest_8_style: str
  nest_9_style: str
  nest_10_style: str
  nest_clearance_above: float
  nest_clearance_below: float

  handover_nest_clearance_above: float
  handover_nest_clearance_below: float
  handover_y_rotation_position: float

  conveyor_clearance_above: float
  conveyor_clearance_below: float

  static_nest_clearance_above: float
  static_nest_clearance_below: float

  io_nest_clearance_above: float
  io_nest_clearance_below: float

  nest_1_sense_input: int
  nest_2_sense_input: int
  nest_3_sense_input: int
  nest_4_sense_input: int
  nest_5_sense_input: int
  nest_6_sense_input: int
  nest_7_sense_input: int
  nest_8_sense_input: int
  nest_9_sense_input: int
  nest_10_sense_input: int

  stacker_base_0: float
  stacker_base_1: float
  stacker_base_2: float

  barcode_base_0: float
  barcode_base_1: float

  stacker_clearance_above: float
  stacker_clearance_below: float

  barcode_scanner: str
  barcode_laser_start: int
  barcode_laser_stop: int
  barcode_velocity: float
  barcode_acceleration: float
  barcode_config_itf_enable: str
  barcode_config_itf_status: str
  barcode_config_itf_length_1: int
  barcode_config_itf_length_2: int
  barcode_config_itf_range: str

  plate_hold_settle_time: int

  door_0_position: float
  door_height: float
  door_overlap_negative: float
  door_overlap_positive: float
  door_gasket_valve: int

  big_door_valve: int

  door_1_valve: int
  door_2_valve: int
  door_3_valve: int
  door_4_valve: int
  door_5_valve: int
  door_6_valve: int
  door_7_valve: int
  door_8_valve: int
  door_1_open_sensor: int
  door_2_open_sensor: int
  door_3_open_sensor: int
  door_4_open_sensor: int
  door_5_open_sensor: int
  door_6_open_sensor: int
  door_7_open_sensor: int
  door_8_open_sensor: int
  door_1_close_sensor: int
  door_2_close_sensor: int
  door_3_close_sensor: int
  door_4_close_sensor: int
  door_5_close_sensor: int
  door_6_close_sensor: int
  door_7_close_sensor: int
  door_8_close_sensor: int
  door_ri_open_sensor: int
  door_ri_close_sensor: int

  gasket_deflate_delay_ms: int
  gasket_inflate_delay_ms: int

  big_door_open_delay_ms: int
  big_door_close_delay_ms: int

  door_open_delay_ms: int
  door_close_delay_ms: int
  door_open_signal_active_level: int

  vac_1_enable: int
  vac_2_enable: int
  vac_1_purge: int
  vac_2_purge: int

  lift_1_enable: int
  lift_2_enable: int

  nest_1_sense: int
  nest_2_sense: int

  vac_3_enable: int
  vac_4_enable: int
  vac_3_purge: int
  vac_4_purge: int

  lift_3_enable: int
  lift_4_enable: int

  nest_3_sense: int
  nest_4_sense: int

  vac_5_enable: int
  vac_6_enable: int
  vac_5_purge: int
  vac_6_purge: int

  lift_5_enable: int
  lift_6_enable: int

  nest_5_sense: int
  nest_6_sense: int

  vac_7_enable: int
  vac_8_enable: int
  vac_7_purge: int
  vac_8_purge: int

  lift_7_enable: int
  lift_8_enable: int

  nest_7_sense: int
  nest_8_sense: int

  active_hotels: int

  nest_rot_home_speed_fast: int
  nest_rot_home_speed_slow: int
  nest_rot_home_acceleration: int
  nest_rot_velocity: int
  nest_rot_acceleration: int
  nest_rot_abort_deceleration: int
  nest_rot_jerk: int
  nest_rot_zero_pos: float

  microspin_door_closed: float
  microspin_door_open: float
  microspin_spindle_home_offset: float
  microspin_bucket_radius_m: float
  microspin_spindle_counts_per_rev: int
  microspin_spindle_position_window: int
  microspin_door_velocity: int
  microspin_door_home_velocity: int
  microspin_door_accel: int
  microspin_door_abort_decel: int
  microspin_door_jerk: int
  microspin_spindle_home_velocity: int
  microspin_spindle_velocity: int
  microspin_spindle_accel: float
  microspin_spindle_decel: float
  microspin_spindle_slow_accel: float
  microspin_spindle_slow_decel: float
  microspin_spindle_abort_decel: float
  microspin_spindle_jerk: int
  microspin_spindle_max_accel: float
  microspin_spindle_max_decel: float
  microspin_idle_spindle_threshold: float
  microspin_bucket_rise_rpm: int

  pico_rot_home_speed_fast: int
  pico_rot_home_speed_slow: int
  pico_rot_home_acceleration: int
  pico_rot_velocity: int
  pico_rot_acceleration: int
  pico_rot_abort_deceleration: int
  pico_rot_jerk: int
  pico_rot_zero_pos: int
  pico_stacker_count: int

  def_plate_height: float
  def_stack_height: float
  def_plate_thickness: float

  jiggle_count: int
  jiggle_size: int

  has_lock_sensor: str

  lock_sensor_input: int

  carousel_max_position: int
  carousel_max_velocity: int
  carousel_max_acceleration: int
  carousel_max_deceleration: int
  carousel_max_jerk: int
  carousel_home_pos_offset: int
  carousel_home_neg_offset: float
  carousel_homing_speed: int
  carousel_home_fast: int
  carousel_home_slow: int
  carousel_home_accel: int
  carousel_stacker_width_default: float
  carousel_small_flag_width: float
  carousel_large_flag_check_distance: float
  carousel_large_flag_width: float
  carousel_ring_numbering: str

  effectuator_extended_position: float
  effectuator_max_position: float
  effectuator_lock_position: float
  effectuator_unlock_position: int
  effectuator_max_velocity: int
  effectuator_max_acceleration: int
  effectuator_abort_deceleration: int
  effectuator_max_jerk: int
  effectuator_home_offset: int
  effectuator_home_fast: int
  effectuator_home_slow: int
  effectuator_home_accel: int

  spatula_max_position: float
  spatula_max_velocity: int
  spatula_measure_velocity: int
  spatula_max_acceleration: int
  spatula_max_jerk: int
  spatula_home_offset: int
  spatula_home_fast: int
  spatula_home_slow: int
  spatula_home_accel: int
  spatula_nest_offset: float
  spatula_measurement_tolerance: float
  spatula_beam_break_height: float

  max_transparency_width_um: int

  spatula_lock_position: float
  spatula_base_position: float

  barcode_max_position: float
  barcode_max_velocity: int
  barcode_max_move_velocity: int
  barcode_abort_deceleration: int
  barcode_max_move_jerk: int
  barcode_max_read_velocity: int
  barcode_home_offset: int
  barcode_home_fast: int
  barcode_home_slow: int
  barcode_home_accel: int

  spatula_slide_safe_position: float
  spatula_slide_barcode_position: float

  nest_safe_rotation_clearance: float

  limit_x_min: float
  limit_x_max: float
  limit_y_min: float
  limit_y_max: float
  limit_z_min: float
  limit_z_max: float
  limit_theta_min: float
  limit_theta_max: float
  limit_g_min: float
  limit_g_max: float
  limit_barcode_min: float
  limit_barcode_max: float

  tundra_door_cycle_active: str
  tundra_door_cycle_time_sec: int
  tundra_door_cycle_open_time_sec: int

  barcode_height_adjust: float

  tundra_outer_door_cycle_active: str
  tundra_outer_door_cycle_time_sec: int
  tundra_outer_door_cycle_open_time_sec: int

  spatula_door_clearance_below: float
  spatula_door_clearance_above: float

  weigh_cell_door_output: int
  weigh_cell_door_input_status_0: int
  weigh_cell_door_input_status_1: int
  weigh_cell_door_input_status_2: int
  weigh_cell_led_red_output: int
  weigh_cell_led_green_output: int
  weigh_cell_led_blue_output: int

  axis_x_home_speed_fast: float
  axis_x_home_speed_slow: float
  axis_x_home_acceleration: float
  axis_x_home_offset: float
  axis_x2_home_offset: float

  tray_to_gantry_0_distance: float

  axis_x2_calibration_adjustment: float
  axis_x_calibration_pos: float
  axis_x_velocity: float
  axis_x_acceleration: float
  axis_x_abort_deceleration: float
  axis_x_jerk: float
  axis_z_home_speed_fast: float
  axis_z_home_speed_slow: float
  axis_z_home_acceleration: float
  axis_z_home_offset: float
  axis_z_home_offset_hardstop: float
  axis_z_home_hardstop_current_ma: int
  axis_z_home_hardstop_current_time_ms: int
  axis_z_velocity: float
  axis_z_acceleration: float
  axis_z_abort_deceleration: float
  axis_z_jerk: float
  axis_barcode_home_speed_fast: float
  axis_barcode_home_speed_slow: float
  axis_barcode_home_acceleration: float
  axis_barcode_home_offset: float
  axis_barcode_velocity: float
  axis_barcode_acceleration: float
  axis_barcode_abort_deceleration: float
  axis_barcode_jerk: float
  axis_gripper_home_speed_fast: float
  axis_gripper_home_speed_slow: float
  axis_gripper_home_acceleration: float
  axis_gripper_home_offset: float
  axis_gripper_home_offset_hardstop: float
  axis_gripper_home_hardstop_current_ma: int
  axis_gripper_home_hardstop_current_time_ms: int
  axis_gripper_close_position: float
  axis_gripper_velocity: float
  axis_gripper_acceleration: float
  axis_gripper_abort_deceleration: float
  axis_gripper_jerk: float

  height_detect_base: float
  height_detect_positive_adjustment: float
  height_detect_negative_adjustment: float
  height_detect_enable_address: int

  barcode_input_number: int

  height_detect_input_number: int

  stacker_code_clearance_above: float
  stacker_code_clearance_below: float
  stacker_code_height: float

  barcode_fixture_height: float
  barcode_fixture_groove_height: float
  barcode_ideal_stacker_tier_1: float
  barcode_ideal_stacker_tier_2: float

  muting_bank_input_1: int
  muting_bank_input_2: int
  muting_bank_input_3: int
  muting_input_1: int
  muting_input_2: int

  tool_head_sel_0: int
  tool_head_sel_1: int
  tool_head_addr_0: int
  tool_head_addr_1: int
  tool_head_addr_2: int
  tool_head_addr_3: int

  ion_bar_air_output: int
  ion_bar_power_output: int

  busybox_mode: str

  microserve_bus_voltage_threshold: float
  microserve_recover_after_estop: str

  randomserve_bus_voltage_threshold: float
  randomserve_recover_after_estop: str

  psp_packet_delay: int

  microspin_spindle_voltage_delay: int
  microspin_bus_voltage_threshold: float

  home_trays_to_hardstop: str

  dc_out_1_default_on: str
  dc_out_2_default_on: str
  dc_out_3_default_on: str

  lid_discard_drop_wait_time_ms: int

  lidvalet_plate_dropped_threshold: int
  lidvalet_hold_time_after_unlid: int
  lidvalet_purge_time_ms: int
  lidvalet_drop_down_time_ms: int
  lidvalet_drop_up_time_ms: int
  lidvalet_pickup_wait_ms: int

  disable_blink_function: str

  oled_blink_time_ms: int

  suppress_copley_debug_statements: str

  prime_waste_chute_installed: str
  prime_waste_chute_position: str

  plate_sensor_high_is_plate_present: str

  stacker_1_speed_multiplier: float
  stacker_2_speed_multiplier: float
  stacker_3_speed_multiplier: float
  stacker_4_speed_multiplier: float
  stacker_5_speed_multiplier: float
  stacker_6_speed_multiplier: float
  stacker_7_speed_multiplier: float
  stacker_8_speed_multiplier: float
  stacker_9_speed_multiplier: float
  stacker_10_speed_multiplier: float
  stacker_11_speed_multiplier: float
  stacker_12_speed_multiplier: float
  stacker_13_speed_multiplier: float
  stacker_14_speed_multiplier: float
  stacker_15_speed_multiplier: float
  stacker_16_speed_multiplier: float
  stacker_17_speed_multiplier: float
  stacker_18_speed_multiplier: float
  stacker_19_speed_multiplier: float
  stacker_20_speed_multiplier: float
  stacker_21_speed_multiplier: float
  stacker_22_speed_multiplier: float
  stacker_23_speed_multiplier: float
  stacker_24_speed_multiplier: float
  stacker_25_speed_multiplier: float
  stacker_26_speed_multiplier: float
  stacker_27_speed_multiplier: float
  stacker_28_speed_multiplier: float
  stacker_1_clearance_above_offset: float
  stacker_2_clearance_above_offset: float
  stacker_3_clearance_above_offset: float
  stacker_4_clearance_above_offset: float
  stacker_5_clearance_above_offset: float
  stacker_6_clearance_above_offset: float
  stacker_7_clearance_above_offset: float
  stacker_8_clearance_above_offset: float
  stacker_9_clearance_above_offset: float
  stacker_10_clearance_above_offset: float
  stacker_11_clearance_above_offset: float
  stacker_12_clearance_above_offset: float
  stacker_13_clearance_above_offset: float
  stacker_14_clearance_above_offset: float
  stacker_15_clearance_above_offset: float
  stacker_16_clearance_above_offset: float
  stacker_17_clearance_above_offset: float
  stacker_18_clearance_above_offset: float
  stacker_19_clearance_above_offset: float
  stacker_20_clearance_above_offset: float
  stacker_21_clearance_above_offset: float
  stacker_22_clearance_above_offset: float
  stacker_23_clearance_above_offset: float
  stacker_24_clearance_above_offset: float
  stacker_25_clearance_above_offset: float
  stacker_26_clearance_above_offset: float
  stacker_27_clearance_above_offset: float
  stacker_28_clearance_above_offset: float

  mfg_door_time_low_limit_ms: int
  mfg_door_time_high_limit_ms: int

  automation_door_time_ms: int

  carousel_home_adj_low_limit: int
  carousel_home_adj_high_limit: int

  store_calibration_fixture_y_distance: float
  store_y_teach_minimum: float
  store_y_teach_maximum: float

  lidvalet_wait_for_lift_rise_ms: int

  @classmethod
  def from_lines(cls, lines: Iterable[str]) -> "TundraStoreSettings":
    """Build from the device's ``settings`` output (``NAME = value`` lines)."""
    data: Dict[str, str] = {}
    for line in lines:
      if "=" in line:
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip()

    values = {}
    missing = []
    for f in fields(cls):
      key = f.name.upper()
      if key not in data:
        missing.append(key)
        continue
      values[f.name] = f.type(data[key]) if f.type in (int, float) else data[key]
    if missing:
      raise ValueError(
        f"settings is missing {len(missing)} expected key(s): "
        + ", ".join(missing[:8])
        + ("..." if len(missing) > 8 else "")
      )
    machine_type = values["machine_type"]
    if machine_type not in KNOWN_MACHINE_TYPES:
      warnings.warn(
        f"unknown TundraStore model {machine_type!r}; please contribute it to "
        "MachineType / KNOWN_MACHINE_TYPES",
        stacklevel=2,
      )
    return cls(**values)
