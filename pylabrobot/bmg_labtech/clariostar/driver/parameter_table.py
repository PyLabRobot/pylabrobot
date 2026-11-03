"""The reader's stored parameters: key, address and size in bytes, read with REQUEST 0x07.

Each key describes the stored value; the table is grouped by what the values are for. The serial
number is not in the table: it is the project number and the reader's number, at
`SERIAL_NUMBER_ADDRESSES`.
"""

from typing import Tuple

# A parameter at address n is reply byte n + 7; values are little endian.
REPLY_OFFSET = 7

# The serial number "NNN-NNNN": the project number (u16 at 154), then the reader's number (156).
SERIAL_NUMBER_ADDRESSES = (154, 156)

PARAMETER_TABLE: Tuple[Tuple[str, int, int], ...] = (
  # The reader's own counters and markers.
  ("eeprom_parameter_list_initialized", 0, 1),
  ("calibration_error_count", 14, 1),
  ("test_run_count", 18, 4),
  ("firmware_version", 158, 2),
  # Fitted options.
  ("luminescence_measurement_channel_count", 128, 1),
  ("photomultiplier_tube_configuration", 129, 1),
  ("fluorescence_measurement_channel_count", 132, 1),
  ("absorbance_measurement_option", 133, 1),
  ("extended_incubator", 134, 1),
  ("alpha_screen_measurement_option", 135, 1),
  ("high_speed_shaking_allowed", 139, 1),
  ("well_1536_measurements_enabled", 140, 1),
  ("bottom_measurement_option", 141, 1),
  ("time_resolved_fluorescence_measurement_channel_count", 142, 1),
  ("fluorescence_polarization_measurement_option", 143, 1),
  ("no_excitation_optic_slide", 144, 1),
  ("integration_unit", 145, 1),
  # Measurement.
  ("luminescence_online_blank_correction_mode", 2, 1),
  ("skip_monochromator_calibration", 10, 1),
  ("absorbance_flash_lamp_high_voltage_intensity", 12, 2),  # 0 brightest to 4095 dimmest
  ("absorbance_0_100_percent_measurement_mode", 31, 1),
  ("measure_well_action_stop_times", 40, 1),
  ("no_k_factor_determination_after_gain_adjustment", 44, 1),
  ("offset_scan_measurement_threshold", 45, 1),  # percent
  ("fluorescence_test_run_excitation_source", 58, 1),
  ("alpha_screen_test_run_excitation_source", 59, 1),
  ("lens_protection_mode", 60, 1),
  ("fluorescence_intensity_online_blank_correction_mode", 61, 1),
  ("top_bottom_switch_position", 64, 1),
  ("focus_adjustment_gain", 74, 2),
  ("flash_lamp_stress_warning_limit", 80, 2),  # 0.1 Hz
  ("auto_gain_calibration_enabled", 82, 1),
  ("flash_off_dark_measurement", 84, 1),
  ("store_reference_channel_as_channel_b", 85, 1),
  ("alternative_flash_rate", 136, 2),  # Hz
  ("alternative_flash_rate_methods", 138, 1),  # bits
  # Auto-gain levels, by photomultiplier tube.
  ("fluorescence_auto_gain_level_1_photomultiplier_tube_1", 160, 2),
  ("fluorescence_auto_gain_level_2_photomultiplier_tube_1", 198, 2),
  ("fluorescence_auto_gain_level_3_photomultiplier_tube_1", 202, 2),
  ("fluorescence_auto_gain_level_4_photomultiplier_tube_1", 204, 2),
  ("fluorescence_auto_gain_level_5_photomultiplier_tube_1", 250, 2),
  ("luminescence_auto_gain_level_1_photomultiplier_tube_1", 246, 2),
  ("luminescence_auto_gain_level_2_photomultiplier_tube_1", 248, 2),
  ("fluorescence_auto_gain_level_1_photomultiplier_tube_2", 90, 2),
  ("fluorescence_auto_gain_level_2_photomultiplier_tube_2", 92, 2),
  ("fluorescence_auto_gain_level_3_photomultiplier_tube_2", 94, 2),
  ("fluorescence_auto_gain_level_4_photomultiplier_tube_2", 96, 2),
  ("fluorescence_auto_gain_level_5_photomultiplier_tube_2", 98, 2),
  ("luminescence_auto_gain_level_1_photomultiplier_tube_2", 100, 2),
  ("luminescence_auto_gain_level_2_photomultiplier_tube_2", 102, 2),
  # Optics: focus, slides and correction factors.
  ("aperture_spoon_to_plate_distance", 30, 1),  # 0.1 mm
  ("do_not_use_smoothed_focus_curve", 41, 1),
  ("infrared_excitation_raw_data_correction_factor", 66, 2),  # x 0.01
  ("infrared_emission_raw_data_correction_factor", 68, 2),  # x 0.01
  ("top_focus_z_correction_filter_type_1", 70, 1),  # 0.01 mm
  ("top_focus_z_correction_filter_type_2", 71, 1),  # 0.01 mm
  ("top_focus_z_correction_filter_type_3", 72, 1),  # 0.01 mm
  ("top_focus_z_correction_filter_type_4", 73, 1),  # 0.01 mm
  ("top_bottom_slide_offset", 162, 2),  # steps
  ("top_focus_z_offset", 164, 2),  # steps
  ("top_optic_focal_length", 170, 2),  # 0.1 mm
  ("bottom_focus_z_offset", 176, 2),  # steps
  ("photomultiplier_tube_slide_offset", 196, 2),  # steps
  ("bottom_optic_focal_length", 206, 2),  # 0.1 mm
  ("aperture_fine_adjustment_z_offset", 217, 1),  # 0.1 mm
  # Plate transport: positions and offsets.
  ("no_xy_transport_calibration_after_shaking", 15, 1),
  ("use_plate_sensor", 33, 1),
  ("use_ultra_fast_xy_motor_profile", 38, 1),
  ("xy_movement_order", 86, 1),
  ("top_measurement_position_x_offset", 150, 2),  # steps
  ("top_measurement_position_y_offset", 152, 2),  # steps
  ("x_transport_lock_position", 172, 2),
  ("y_transport_lock_position", 174, 2),
  ("stack_1_carrier_x_position", 178, 2),
  ("stack_1_carrier_y_position", 180, 2),
  ("stack_2_carrier_x_position", 182, 2),
  ("stack_2_carrier_y_position", 184, 2),
  ("bottom_measurement_position_x_offset", 188, 2),  # steps
  ("bottom_measurement_position_y_offset", 190, 2),  # steps
  ("spectrometer_position_x_offset", 192, 2),  # steps
  ("spectrometer_position_y_offset", 194, 2),  # steps
  ("z_plate_detection_needle_transport_offset", 200, 2),  # steps
  ("plate_carrier_thickness", 208, 2),  # 0.01 mm
  ("injection_position_x_offset", 228, 2),
  ("injection_position_y_offset", 230, 2),
  # Pumps and injection.
  ("pump_1_prime_speed", 4, 1),
  ("pump_2_prime_speed", 5, 1),
  ("pump_1_invert_dispensing_mode", 6, 1),
  ("pump_2_invert_dispensing_mode", 7, 1),
  ("pump_1_prime_stroke_volume", 8, 1),  # percent of the syringe volume
  ("pump_2_prime_stroke_volume", 9, 1),  # percent of the syringe volume
  ("injection_needle_to_plate_distance", 16, 1),  # 0.1 mm
  ("do_not_use_prime_sensors", 17, 1),
  ("injection_pull_back_air_volume", 34, 1),  # ul
  ("pump_1_syringe_volume", 35, 1),
  ("pump_2_syringe_volume", 36, 1),
  ("inverted_smart_dispensing_mode", 79, 1),
  # Barcode readers.
  ("front_barcode_y_position", 48, 2),  # steps
  ("right_barcode_x_position", 50, 2),  # steps
  ("front_barcode_scanning_resolution", 54, 2),  # steps per mm
  ("right_barcode_scanning_resolution", 56, 2),  # steps per mm
  # Fans and temperature.
  ("maximum_temperature_errors_before_warning", 3, 1),
  ("power_on_incubation", 24, 2),  # 0 off, 1 temperature monitoring only, else the target in 0.1 C
  ("fan_1_speed", 39, 1),  # percent, 0 controlled by the reader
  ("fan_2_speed", 76, 2),  # percent, above 100 controlled by the reader
  ("temperature_control_1_offset", 166, 2),  # 0.1 C
  ("temperature_control_2_offset", 168, 2),  # 0.1 C
  ("fan_2_busy_speed", 210, 1),  # percent
  ("fan_2_idle_speed", 211, 1),  # percent
  ("fan_2_cover_open_speed", 212, 1),  # percent
  ("fan_2_power_on_speed", 213, 1),  # percent
  ("fan_2_power_on_time", 214, 2),  # s
  ("fan_2_after_test_run_speed", 216, 1),  # percent
  ("fan_2_after_test_run_time", 218, 2),  # s
  ("fan_2_speed_above_threshold_temperature_1", 232, 1),  # percent
  ("fan_2_speed_above_threshold_temperature_2", 233, 1),  # percent
  ("fan_threshold_temperature_1", 234, 2),  # 0.1 C
  ("fan_threshold_temperature_2", 236, 2),  # 0.1 C
  ("fan_2_after_software_termination_speed", 238, 1),  # percent
  ("fan_2_after_software_termination_time", 240, 2),  # s
  ("temperature_sensor_1_offset", 243, 1),  # 0.1 C
  ("temperature_sensor_2_offset", 244, 1),  # 0.1 C
  # Device.
  ("exhibition_demo_mode", 22, 2),  # bits
  ("disable_door_sensor_monitoring", 29, 1),
  ("data_logging_mode", 42, 1),  # bits
  ("data_logging_mode_2", 43, 1),  # bits
)

# The parameters that can be negative; the rest are unsigned.
SIGNED_PARAMETERS = (
  "top_bottom_slide_offset",
  "temperature_control_1_offset",
  "temperature_control_2_offset",
  "x_transport_lock_position",
  "y_transport_lock_position",
  "spectrometer_position_x_offset",
  "temperature_sensor_1_offset",
  "temperature_sensor_2_offset",
)
