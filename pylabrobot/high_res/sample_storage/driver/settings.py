"""Typed, immutable view of a HighRes sample store's on-device settings.

The device exposes its full calibration/configuration via the ``settings``
command as ``NAME = value`` text. Each key is surfaced here as one explicitly
typed attribute (the device ``NAME`` lower-cased); types are inferred from the
device's own values. A :class:`HighResSampleStorageSettings` is loaded whole from the
device (or a capture) and is frozen once built. Firmware/model-specific keys are
preserved in :attr:`HighResSampleStorageSettings.raw`; known keys that a device
does not report remain ``None``.
"""

import logging
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, get_args

logger = logging.getLogger(__name__)


# Known family names reported by the device's MACHINE_TYPE setting. The wire
# value remains a string because firmware variants may introduce additional
# model names without requiring a driver release just to parse their settings.
MachineType = str
KNOWN_MACHINE_TYPES: Tuple[str, ...] = (
  "AmbiStore",
  "SteriStore",
  "SteriStore2",
  "TundraStore",
)


@dataclass(frozen=True)
class HighResSampleStorageSettings:
  """Known on-device settings plus the complete raw key/value response."""

  product_name: Optional[str] = None
  product_description: Optional[str] = None

  serial_number: Optional[str] = None

  machine_type: Optional[MachineType] = None

  rest_server_port: Optional[int] = None

  syslog_server: Optional[str] = None
  syslog_level: Optional[int] = None

  internal_log_level: Optional[int] = None

  carousel_home_speed_fast: Optional[float] = None
  carousel_home_speed_slow: Optional[float] = None
  carousel_home_acceleration: Optional[float] = None
  carousel_velocity: Optional[float] = None
  carousel_idle_velocity: Optional[float] = None
  carousel_acceleration: Optional[float] = None
  carousel_abort_deceleration: Optional[float] = None
  carousel_jerk: Optional[float] = None
  carousel_final_drive_jerk: Optional[float] = None
  carousel_stacker_0_pos: Optional[float] = None
  carousel_stacker_1_pos: Optional[float] = None
  carousel_stacker_2_pos: Optional[float] = None
  carousel_stacker_count: Optional[int] = None
  carousel_count: Optional[int] = None
  carousel_calibration_offset: Optional[float] = None

  spatula_home_speed_fast: Optional[float] = None
  spatula_home_speed_slow: Optional[float] = None
  spatula_home_acceleration: Optional[float] = None
  spatula_velocity: Optional[float] = None
  spatula_velocity_with_plate: Optional[float] = None
  spatula_acceleration: Optional[float] = None
  spatula_abort_deceleration: Optional[float] = None
  spatula_jerk: Optional[float] = None
  spatula_rot_home_speed_fast: Optional[float] = None
  spatula_rot_home_speed_slow: Optional[float] = None
  spatula_rot_home_acceleration: Optional[float] = None
  spatula_rot_velocity: Optional[float] = None
  spatula_rot_acceleration: Optional[float] = None
  spatula_rot_abort_deceleration: Optional[float] = None
  spatula_rot_jerk: Optional[float] = None
  spatula_rot_zero_pos: Optional[float] = None
  spatula_rot_stack_pos_0: Optional[float] = None
  spatula_rot_stack_pos_1: Optional[float] = None
  spatula_rot_stack_pos_2: Optional[float] = None
  spatula_rot_nest_1_pos: Optional[float] = None
  spatula_rot_nest_2_pos: Optional[float] = None
  spatula_rot_nest_3_pos: Optional[float] = None
  spatula_rot_nest_4_pos: Optional[float] = None
  spatula_rot_nest_5_pos: Optional[float] = None
  spatula_rot_nest_6_pos: Optional[float] = None
  spatula_rot_nest_7_pos: Optional[float] = None
  spatula_rot_nest_8_pos: Optional[float] = None
  spatula_rot_nest_9_pos: Optional[float] = None
  spatula_rot_nest_10_pos: Optional[float] = None
  spatula_rot_nest_21_pos: Optional[float] = None
  spatula_rot_nest_22_pos: Optional[float] = None
  spatula_rot_nest_23_pos: Optional[float] = None
  spatula_rot_nest_24_pos: Optional[float] = None
  spatula_rot_nest_51_pos: Optional[float] = None
  spatula_rot_nest_52_pos: Optional[float] = None
  spatula_rot_nest_61_pos: Optional[float] = None
  spatula_rot_nest_62_pos: Optional[float] = None
  spatula_rot_nest_63_pos: Optional[float] = None
  spatula_rot_nest_64_pos: Optional[float] = None
  spatula_rot_nest_65_pos: Optional[float] = None
  spatula_rot_nest_66_pos: Optional[float] = None
  spatula_rot_nest_67_pos: Optional[float] = None
  spatula_rot_nest_68_pos: Optional[float] = None
  spatula_rot_nest_69_pos: Optional[float] = None
  spatula_slide_home_speed_fast: Optional[float] = None
  spatula_slide_home_speed_slow: Optional[float] = None
  spatula_slide_home_acceleration: Optional[float] = None
  spatula_slide_home_offset: Optional[float] = None
  spatula_slide_velocity: Optional[float] = None
  spatula_slide_acceleration: Optional[float] = None
  spatula_slide_abort_deceleration: Optional[float] = None
  spatula_slide_jerk: Optional[float] = None
  spatula_slide_in_pos_0: Optional[float] = None
  spatula_slide_in_pos_1: Optional[float] = None
  spatula_slide_in_pos_2: Optional[float] = None
  spatula_slide_nest_1_pos: Optional[float] = None
  spatula_slide_nest_2_pos: Optional[float] = None
  spatula_slide_nest_3_pos: Optional[float] = None
  spatula_slide_nest_4_pos: Optional[float] = None
  spatula_slide_nest_5_pos: Optional[float] = None
  spatula_slide_nest_6_pos: Optional[float] = None
  spatula_slide_nest_7_pos: Optional[float] = None
  spatula_slide_nest_8_pos: Optional[float] = None
  spatula_slide_nest_9_pos: Optional[float] = None
  spatula_slide_nest_10_pos: Optional[float] = None
  spatula_slide_nest_21_pos: Optional[float] = None
  spatula_slide_nest_22_pos: Optional[float] = None
  spatula_slide_nest_23_pos: Optional[float] = None
  spatula_slide_nest_24_pos: Optional[float] = None
  spatula_slide_nest_51_pos: Optional[float] = None
  spatula_slide_nest_52_pos: Optional[float] = None
  spatula_slide_nest_61_pos: Optional[float] = None
  spatula_slide_nest_62_pos: Optional[float] = None
  spatula_slide_nest_63_pos: Optional[float] = None
  spatula_slide_nest_64_pos: Optional[float] = None
  spatula_slide_nest_65_pos: Optional[float] = None
  spatula_slide_nest_66_pos: Optional[float] = None
  spatula_slide_nest_67_pos: Optional[float] = None
  spatula_slide_nest_68_pos: Optional[float] = None
  spatula_slide_nest_69_pos: Optional[float] = None
  spatula_valve_hold: Optional[int] = None
  spatula_plate_sensor: Optional[int] = None
  spatula_plate_release_sensor: Optional[int] = None

  inner_user_door_sensor: Optional[int] = None

  door_open_sensor_output: Optional[int] = None

  nest_count: Optional[int] = None
  nest_1_height: Optional[float] = None
  nest_2_height: Optional[float] = None
  nest_3_height: Optional[float] = None
  nest_4_height: Optional[float] = None
  nest_5_height: Optional[float] = None
  nest_6_height: Optional[float] = None
  nest_7_height: Optional[float] = None
  nest_8_height: Optional[float] = None
  nest_9_height: Optional[float] = None
  nest_10_height: Optional[float] = None
  nest_21_height: Optional[float] = None
  nest_22_height: Optional[float] = None
  nest_23_height: Optional[float] = None
  nest_24_height: Optional[float] = None
  nest_51_height: Optional[float] = None
  nest_52_height: Optional[float] = None
  nest_61_height: Optional[float] = None
  nest_62_height: Optional[float] = None
  nest_63_height: Optional[float] = None
  nest_64_height: Optional[float] = None
  nest_65_height: Optional[float] = None
  nest_66_height: Optional[float] = None
  nest_67_height: Optional[float] = None
  nest_68_height: Optional[float] = None
  nest_69_height: Optional[float] = None
  nest_1_style: Optional[str] = None
  nest_2_style: Optional[str] = None
  nest_3_style: Optional[str] = None
  nest_4_style: Optional[str] = None
  nest_5_style: Optional[str] = None
  nest_6_style: Optional[str] = None
  nest_7_style: Optional[str] = None
  nest_8_style: Optional[str] = None
  nest_9_style: Optional[str] = None
  nest_10_style: Optional[str] = None
  nest_clearance_above: Optional[float] = None
  nest_clearance_below: Optional[float] = None

  handover_nest_clearance_above: Optional[float] = None
  handover_nest_clearance_below: Optional[float] = None
  handover_y_rotation_position: Optional[float] = None

  conveyor_clearance_above: Optional[float] = None
  conveyor_clearance_below: Optional[float] = None

  static_nest_clearance_above: Optional[float] = None
  static_nest_clearance_below: Optional[float] = None

  io_nest_clearance_above: Optional[float] = None
  io_nest_clearance_below: Optional[float] = None

  nest_1_sense_input: Optional[int] = None
  nest_2_sense_input: Optional[int] = None
  nest_3_sense_input: Optional[int] = None
  nest_4_sense_input: Optional[int] = None
  nest_5_sense_input: Optional[int] = None
  nest_6_sense_input: Optional[int] = None
  nest_7_sense_input: Optional[int] = None
  nest_8_sense_input: Optional[int] = None
  nest_9_sense_input: Optional[int] = None
  nest_10_sense_input: Optional[int] = None

  stacker_base_0: Optional[float] = None
  stacker_base_1: Optional[float] = None
  stacker_base_2: Optional[float] = None

  barcode_base_0: Optional[float] = None
  barcode_base_1: Optional[float] = None

  stacker_clearance_above: Optional[float] = None
  stacker_clearance_below: Optional[float] = None

  barcode_scanner: Optional[str] = None
  barcode_laser_start: Optional[int] = None
  barcode_laser_stop: Optional[int] = None
  barcode_velocity: Optional[float] = None
  barcode_acceleration: Optional[float] = None
  barcode_config_itf_enable: Optional[str] = None
  barcode_config_itf_status: Optional[str] = None
  barcode_config_itf_length_1: Optional[int] = None
  barcode_config_itf_length_2: Optional[int] = None
  barcode_config_itf_range: Optional[str] = None

  plate_hold_settle_time: Optional[int] = None

  door_0_position: Optional[float] = None
  door_height: Optional[float] = None
  door_overlap_negative: Optional[float] = None
  door_overlap_positive: Optional[float] = None
  door_gasket_valve: Optional[int] = None

  big_door_valve: Optional[int] = None

  door_1_valve: Optional[int] = None
  door_2_valve: Optional[int] = None
  door_3_valve: Optional[int] = None
  door_4_valve: Optional[int] = None
  door_5_valve: Optional[int] = None
  door_6_valve: Optional[int] = None
  door_7_valve: Optional[int] = None
  door_8_valve: Optional[int] = None
  door_1_open_sensor: Optional[int] = None
  door_2_open_sensor: Optional[int] = None
  door_3_open_sensor: Optional[int] = None
  door_4_open_sensor: Optional[int] = None
  door_5_open_sensor: Optional[int] = None
  door_6_open_sensor: Optional[int] = None
  door_7_open_sensor: Optional[int] = None
  door_8_open_sensor: Optional[int] = None
  door_1_close_sensor: Optional[int] = None
  door_2_close_sensor: Optional[int] = None
  door_3_close_sensor: Optional[int] = None
  door_4_close_sensor: Optional[int] = None
  door_5_close_sensor: Optional[int] = None
  door_6_close_sensor: Optional[int] = None
  door_7_close_sensor: Optional[int] = None
  door_8_close_sensor: Optional[int] = None
  door_ri_open_sensor: Optional[int] = None
  door_ri_close_sensor: Optional[int] = None

  gasket_deflate_delay_ms: Optional[int] = None
  gasket_inflate_delay_ms: Optional[int] = None

  big_door_open_delay_ms: Optional[int] = None
  big_door_close_delay_ms: Optional[int] = None

  door_open_delay_ms: Optional[int] = None
  door_close_delay_ms: Optional[int] = None
  door_open_signal_active_level: Optional[int] = None

  vac_1_enable: Optional[int] = None
  vac_2_enable: Optional[int] = None
  vac_1_purge: Optional[int] = None
  vac_2_purge: Optional[int] = None

  lift_1_enable: Optional[int] = None
  lift_2_enable: Optional[int] = None

  nest_1_sense: Optional[int] = None
  nest_2_sense: Optional[int] = None

  vac_3_enable: Optional[int] = None
  vac_4_enable: Optional[int] = None
  vac_3_purge: Optional[int] = None
  vac_4_purge: Optional[int] = None

  lift_3_enable: Optional[int] = None
  lift_4_enable: Optional[int] = None

  nest_3_sense: Optional[int] = None
  nest_4_sense: Optional[int] = None

  vac_5_enable: Optional[int] = None
  vac_6_enable: Optional[int] = None
  vac_5_purge: Optional[int] = None
  vac_6_purge: Optional[int] = None

  lift_5_enable: Optional[int] = None
  lift_6_enable: Optional[int] = None

  nest_5_sense: Optional[int] = None
  nest_6_sense: Optional[int] = None

  vac_7_enable: Optional[int] = None
  vac_8_enable: Optional[int] = None
  vac_7_purge: Optional[int] = None
  vac_8_purge: Optional[int] = None

  lift_7_enable: Optional[int] = None
  lift_8_enable: Optional[int] = None

  nest_7_sense: Optional[int] = None
  nest_8_sense: Optional[int] = None

  active_hotels: Optional[int] = None

  nest_rot_home_speed_fast: Optional[int] = None
  nest_rot_home_speed_slow: Optional[int] = None
  nest_rot_home_acceleration: Optional[int] = None
  nest_rot_velocity: Optional[int] = None
  nest_rot_acceleration: Optional[int] = None
  nest_rot_abort_deceleration: Optional[int] = None
  nest_rot_jerk: Optional[int] = None
  nest_rot_zero_pos: Optional[float] = None

  microspin_door_closed: Optional[float] = None
  microspin_door_open: Optional[float] = None
  microspin_spindle_home_offset: Optional[float] = None
  microspin_bucket_radius_m: Optional[float] = None
  microspin_spindle_counts_per_rev: Optional[int] = None
  microspin_spindle_position_window: Optional[int] = None
  microspin_door_velocity: Optional[int] = None
  microspin_door_home_velocity: Optional[int] = None
  microspin_door_accel: Optional[int] = None
  microspin_door_abort_decel: Optional[int] = None
  microspin_door_jerk: Optional[int] = None
  microspin_spindle_home_velocity: Optional[int] = None
  microspin_spindle_velocity: Optional[int] = None
  microspin_spindle_accel: Optional[float] = None
  microspin_spindle_decel: Optional[float] = None
  microspin_spindle_slow_accel: Optional[float] = None
  microspin_spindle_slow_decel: Optional[float] = None
  microspin_spindle_abort_decel: Optional[float] = None
  microspin_spindle_jerk: Optional[int] = None
  microspin_spindle_max_accel: Optional[float] = None
  microspin_spindle_max_decel: Optional[float] = None
  microspin_idle_spindle_threshold: Optional[float] = None
  microspin_bucket_rise_rpm: Optional[int] = None

  pico_rot_home_speed_fast: Optional[int] = None
  pico_rot_home_speed_slow: Optional[int] = None
  pico_rot_home_acceleration: Optional[int] = None
  pico_rot_velocity: Optional[int] = None
  pico_rot_acceleration: Optional[int] = None
  pico_rot_abort_deceleration: Optional[int] = None
  pico_rot_jerk: Optional[int] = None
  pico_rot_zero_pos: Optional[int] = None
  pico_stacker_count: Optional[int] = None

  def_plate_height: Optional[float] = None
  def_stack_height: Optional[float] = None
  def_plate_thickness: Optional[float] = None

  jiggle_count: Optional[int] = None
  jiggle_size: Optional[int] = None

  has_lock_sensor: Optional[str] = None

  lock_sensor_input: Optional[int] = None

  carousel_max_position: Optional[int] = None
  carousel_max_velocity: Optional[int] = None
  carousel_max_acceleration: Optional[int] = None
  carousel_max_deceleration: Optional[int] = None
  carousel_max_jerk: Optional[int] = None
  carousel_home_pos_offset: Optional[int] = None
  carousel_home_neg_offset: Optional[float] = None
  carousel_homing_speed: Optional[int] = None
  carousel_home_fast: Optional[int] = None
  carousel_home_slow: Optional[int] = None
  carousel_home_accel: Optional[int] = None
  carousel_stacker_width_default: Optional[float] = None
  carousel_small_flag_width: Optional[float] = None
  carousel_large_flag_check_distance: Optional[float] = None
  carousel_large_flag_width: Optional[float] = None
  carousel_ring_numbering: Optional[str] = None

  effectuator_extended_position: Optional[float] = None
  effectuator_max_position: Optional[float] = None
  effectuator_lock_position: Optional[float] = None
  effectuator_unlock_position: Optional[int] = None
  effectuator_max_velocity: Optional[int] = None
  effectuator_max_acceleration: Optional[int] = None
  effectuator_abort_deceleration: Optional[int] = None
  effectuator_max_jerk: Optional[int] = None
  effectuator_home_offset: Optional[int] = None
  effectuator_home_fast: Optional[int] = None
  effectuator_home_slow: Optional[int] = None
  effectuator_home_accel: Optional[int] = None

  spatula_max_position: Optional[float] = None
  spatula_max_velocity: Optional[int] = None
  spatula_measure_velocity: Optional[int] = None
  spatula_max_acceleration: Optional[int] = None
  spatula_max_jerk: Optional[int] = None
  spatula_home_offset: Optional[int] = None
  spatula_home_fast: Optional[int] = None
  spatula_home_slow: Optional[int] = None
  spatula_home_accel: Optional[int] = None
  spatula_nest_offset: Optional[float] = None
  spatula_measurement_tolerance: Optional[float] = None
  spatula_beam_break_height: Optional[float] = None

  max_transparency_width_um: Optional[int] = None

  spatula_lock_position: Optional[float] = None
  spatula_base_position: Optional[float] = None

  barcode_max_position: Optional[float] = None
  barcode_max_velocity: Optional[int] = None
  barcode_max_move_velocity: Optional[int] = None
  barcode_abort_deceleration: Optional[int] = None
  barcode_max_move_jerk: Optional[int] = None
  barcode_max_read_velocity: Optional[int] = None
  barcode_home_offset: Optional[int] = None
  barcode_home_fast: Optional[int] = None
  barcode_home_slow: Optional[int] = None
  barcode_home_accel: Optional[int] = None

  spatula_slide_safe_position: Optional[float] = None
  spatula_slide_barcode_position: Optional[float] = None

  nest_safe_rotation_clearance: Optional[float] = None

  limit_x_min: Optional[float] = None
  limit_x_max: Optional[float] = None
  limit_y_min: Optional[float] = None
  limit_y_max: Optional[float] = None
  limit_z_min: Optional[float] = None
  limit_z_max: Optional[float] = None
  limit_theta_min: Optional[float] = None
  limit_theta_max: Optional[float] = None
  limit_g_min: Optional[float] = None
  limit_g_max: Optional[float] = None
  limit_barcode_min: Optional[float] = None
  limit_barcode_max: Optional[float] = None

  tundra_door_cycle_active: Optional[str] = None
  tundra_door_cycle_time_sec: Optional[int] = None
  tundra_door_cycle_open_time_sec: Optional[int] = None

  barcode_height_adjust: Optional[float] = None

  tundra_outer_door_cycle_active: Optional[str] = None
  tundra_outer_door_cycle_time_sec: Optional[int] = None
  tundra_outer_door_cycle_open_time_sec: Optional[int] = None

  spatula_door_clearance_below: Optional[float] = None
  spatula_door_clearance_above: Optional[float] = None

  weigh_cell_door_output: Optional[int] = None
  weigh_cell_door_input_status_0: Optional[int] = None
  weigh_cell_door_input_status_1: Optional[int] = None
  weigh_cell_door_input_status_2: Optional[int] = None
  weigh_cell_led_red_output: Optional[int] = None
  weigh_cell_led_green_output: Optional[int] = None
  weigh_cell_led_blue_output: Optional[int] = None

  axis_x_home_speed_fast: Optional[float] = None
  axis_x_home_speed_slow: Optional[float] = None
  axis_x_home_acceleration: Optional[float] = None
  axis_x_home_offset: Optional[float] = None
  axis_x2_home_offset: Optional[float] = None

  tray_to_gantry_0_distance: Optional[float] = None

  axis_x2_calibration_adjustment: Optional[float] = None
  axis_x_calibration_pos: Optional[float] = None
  axis_x_velocity: Optional[float] = None
  axis_x_acceleration: Optional[float] = None
  axis_x_abort_deceleration: Optional[float] = None
  axis_x_jerk: Optional[float] = None
  axis_z_home_speed_fast: Optional[float] = None
  axis_z_home_speed_slow: Optional[float] = None
  axis_z_home_acceleration: Optional[float] = None
  axis_z_home_offset: Optional[float] = None
  axis_z_home_offset_hardstop: Optional[float] = None
  axis_z_home_hardstop_current_ma: Optional[int] = None
  axis_z_home_hardstop_current_time_ms: Optional[int] = None
  axis_z_velocity: Optional[float] = None
  axis_z_acceleration: Optional[float] = None
  axis_z_abort_deceleration: Optional[float] = None
  axis_z_jerk: Optional[float] = None
  axis_barcode_home_speed_fast: Optional[float] = None
  axis_barcode_home_speed_slow: Optional[float] = None
  axis_barcode_home_acceleration: Optional[float] = None
  axis_barcode_home_offset: Optional[float] = None
  axis_barcode_velocity: Optional[float] = None
  axis_barcode_acceleration: Optional[float] = None
  axis_barcode_abort_deceleration: Optional[float] = None
  axis_barcode_jerk: Optional[float] = None
  axis_gripper_home_speed_fast: Optional[float] = None
  axis_gripper_home_speed_slow: Optional[float] = None
  axis_gripper_home_acceleration: Optional[float] = None
  axis_gripper_home_offset: Optional[float] = None
  axis_gripper_home_offset_hardstop: Optional[float] = None
  axis_gripper_home_hardstop_current_ma: Optional[int] = None
  axis_gripper_home_hardstop_current_time_ms: Optional[int] = None
  axis_gripper_close_position: Optional[float] = None
  axis_gripper_velocity: Optional[float] = None
  axis_gripper_acceleration: Optional[float] = None
  axis_gripper_abort_deceleration: Optional[float] = None
  axis_gripper_jerk: Optional[float] = None

  height_detect_base: Optional[float] = None
  height_detect_positive_adjustment: Optional[float] = None
  height_detect_negative_adjustment: Optional[float] = None
  height_detect_enable_address: Optional[int] = None

  barcode_input_number: Optional[int] = None

  height_detect_input_number: Optional[int] = None

  stacker_code_clearance_above: Optional[float] = None
  stacker_code_clearance_below: Optional[float] = None
  stacker_code_height: Optional[float] = None

  barcode_fixture_height: Optional[float] = None
  barcode_fixture_groove_height: Optional[float] = None
  barcode_ideal_stacker_tier_1: Optional[float] = None
  barcode_ideal_stacker_tier_2: Optional[float] = None

  muting_bank_input_1: Optional[int] = None
  muting_bank_input_2: Optional[int] = None
  muting_bank_input_3: Optional[int] = None
  muting_input_1: Optional[int] = None
  muting_input_2: Optional[int] = None

  tool_head_sel_0: Optional[int] = None
  tool_head_sel_1: Optional[int] = None
  tool_head_addr_0: Optional[int] = None
  tool_head_addr_1: Optional[int] = None
  tool_head_addr_2: Optional[int] = None
  tool_head_addr_3: Optional[int] = None

  ion_bar_air_output: Optional[int] = None
  ion_bar_power_output: Optional[int] = None

  busybox_mode: Optional[str] = None

  microserve_bus_voltage_threshold: Optional[float] = None
  microserve_recover_after_estop: Optional[str] = None

  randomserve_bus_voltage_threshold: Optional[float] = None
  randomserve_recover_after_estop: Optional[str] = None

  psp_packet_delay: Optional[int] = None

  microspin_spindle_voltage_delay: Optional[int] = None
  microspin_bus_voltage_threshold: Optional[float] = None

  home_trays_to_hardstop: Optional[str] = None

  dc_out_1_default_on: Optional[str] = None
  dc_out_2_default_on: Optional[str] = None
  dc_out_3_default_on: Optional[str] = None

  lid_discard_drop_wait_time_ms: Optional[int] = None

  lidvalet_plate_dropped_threshold: Optional[int] = None
  lidvalet_hold_time_after_unlid: Optional[int] = None
  lidvalet_purge_time_ms: Optional[int] = None
  lidvalet_drop_down_time_ms: Optional[int] = None
  lidvalet_drop_up_time_ms: Optional[int] = None
  lidvalet_pickup_wait_ms: Optional[int] = None

  disable_blink_function: Optional[str] = None

  oled_blink_time_ms: Optional[int] = None

  suppress_copley_debug_statements: Optional[str] = None

  prime_waste_chute_installed: Optional[str] = None
  prime_waste_chute_position: Optional[str] = None

  plate_sensor_high_is_plate_present: Optional[str] = None

  stacker_1_speed_multiplier: Optional[float] = None
  stacker_2_speed_multiplier: Optional[float] = None
  stacker_3_speed_multiplier: Optional[float] = None
  stacker_4_speed_multiplier: Optional[float] = None
  stacker_5_speed_multiplier: Optional[float] = None
  stacker_6_speed_multiplier: Optional[float] = None
  stacker_7_speed_multiplier: Optional[float] = None
  stacker_8_speed_multiplier: Optional[float] = None
  stacker_9_speed_multiplier: Optional[float] = None
  stacker_10_speed_multiplier: Optional[float] = None
  stacker_11_speed_multiplier: Optional[float] = None
  stacker_12_speed_multiplier: Optional[float] = None
  stacker_13_speed_multiplier: Optional[float] = None
  stacker_14_speed_multiplier: Optional[float] = None
  stacker_15_speed_multiplier: Optional[float] = None
  stacker_16_speed_multiplier: Optional[float] = None
  stacker_17_speed_multiplier: Optional[float] = None
  stacker_18_speed_multiplier: Optional[float] = None
  stacker_19_speed_multiplier: Optional[float] = None
  stacker_20_speed_multiplier: Optional[float] = None
  stacker_21_speed_multiplier: Optional[float] = None
  stacker_22_speed_multiplier: Optional[float] = None
  stacker_23_speed_multiplier: Optional[float] = None
  stacker_24_speed_multiplier: Optional[float] = None
  stacker_25_speed_multiplier: Optional[float] = None
  stacker_26_speed_multiplier: Optional[float] = None
  stacker_27_speed_multiplier: Optional[float] = None
  stacker_28_speed_multiplier: Optional[float] = None
  stacker_1_clearance_above_offset: Optional[float] = None
  stacker_2_clearance_above_offset: Optional[float] = None
  stacker_3_clearance_above_offset: Optional[float] = None
  stacker_4_clearance_above_offset: Optional[float] = None
  stacker_5_clearance_above_offset: Optional[float] = None
  stacker_6_clearance_above_offset: Optional[float] = None
  stacker_7_clearance_above_offset: Optional[float] = None
  stacker_8_clearance_above_offset: Optional[float] = None
  stacker_9_clearance_above_offset: Optional[float] = None
  stacker_10_clearance_above_offset: Optional[float] = None
  stacker_11_clearance_above_offset: Optional[float] = None
  stacker_12_clearance_above_offset: Optional[float] = None
  stacker_13_clearance_above_offset: Optional[float] = None
  stacker_14_clearance_above_offset: Optional[float] = None
  stacker_15_clearance_above_offset: Optional[float] = None
  stacker_16_clearance_above_offset: Optional[float] = None
  stacker_17_clearance_above_offset: Optional[float] = None
  stacker_18_clearance_above_offset: Optional[float] = None
  stacker_19_clearance_above_offset: Optional[float] = None
  stacker_20_clearance_above_offset: Optional[float] = None
  stacker_21_clearance_above_offset: Optional[float] = None
  stacker_22_clearance_above_offset: Optional[float] = None
  stacker_23_clearance_above_offset: Optional[float] = None
  stacker_24_clearance_above_offset: Optional[float] = None
  stacker_25_clearance_above_offset: Optional[float] = None
  stacker_26_clearance_above_offset: Optional[float] = None
  stacker_27_clearance_above_offset: Optional[float] = None
  stacker_28_clearance_above_offset: Optional[float] = None

  mfg_door_time_low_limit_ms: Optional[int] = None
  mfg_door_time_high_limit_ms: Optional[int] = None

  automation_door_time_ms: Optional[int] = None

  carousel_home_adj_low_limit: Optional[int] = None
  carousel_home_adj_high_limit: Optional[int] = None

  store_calibration_fixture_y_distance: Optional[float] = None
  store_y_teach_minimum: Optional[float] = None
  store_y_teach_maximum: Optional[float] = None

  lidvalet_wait_for_lift_rise_ms: Optional[int] = None

  raw: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}), repr=False)

  @property
  def extra(self) -> Dict[str, str]:
    """Firmware keys that do not have a typed attribute in this version."""
    known = {f.name.upper() for f in fields(self) if f.name != "raw"}
    return {key: value for key, value in self.raw.items() if key not in known}

  @classmethod
  def from_lines(cls, lines: Iterable[str]) -> "HighResSampleStorageSettings":
    """Build from the device's ``settings`` output (``NAME = value`` lines)."""
    data: Dict[str, str] = {}
    for line in lines:
      if "=" in line:
        key, _, value = line.partition("=")
        data[key.strip().upper()] = value.strip()

    values: Dict[str, Any] = {"raw": MappingProxyType(dict(data))}
    for f in fields(cls):
      if f.name == "raw":
        continue
      key = f.name.upper()
      if key not in data:
        continue
      value_type = next((arg for arg in get_args(f.type) if arg is not type(None)), str)
      if value_type is int:
        values[f.name] = int(data[key])
      elif value_type is float:
        values[f.name] = float(data[key])
      else:
        values[f.name] = data[key]
    machine_type = values.get("machine_type")
    if machine_type is not None and machine_type not in KNOWN_MACHINE_TYPES:
      logger.warning(
        "Unknown HighRes sample-store model %r; preserving all settings as raw values",
        machine_type,
      )
    return cls(**values)
