"""Shared constructor-based test fixtures for the Celigo driver."""

from dataclasses import replace
from typing import Any, Optional, Tuple
from unittest.mock import patch

from pylabrobot.revvity.celigo.camera import CameraFrame
from pylabrobot.revvity.celigo.celigo import Celigo
from pylabrobot.revvity.celigo.config import (
  CalibrationConfig,
  CeligoConfig,
  CeligoHardwareConfig,
  FilterWheelConfig,
  GalvoAxisOpticalCalibration,
  GalvoConfig,
  GalvoMagnificationCalibration,
  GalvoOpticalCalibration,
  HardwareDefaultConfig,
  LinearAxisConfig,
  NavigationConfig,
)


class FakeCamera:
  """In-memory camera that satisfies the lifecycle expected by ``Celigo`` tests."""

  def __init__(self, sdk_library: Optional[str] = None) -> None:
    self.sdk_library = sdk_library
    self.is_open = False
    self.width = 1
    self.height = 1
    self.bit_depth = 8
    self.x_offset = 0
    self.y_offset = 0
    self.frame_rate = 1.0
    self.exposure_ms = 1.0
    self.gain = 0.0

  async def setup(self) -> None:
    self.is_open = True

  async def stop(self) -> None:
    self.is_open = False

  async def set_exposure(self, exposure_ms: float) -> float:
    self.exposure_ms = exposure_ms
    return exposure_ms

  async def set_gain(self, gain: float) -> float:
    self.gain = gain
    return gain

  async def set_frame_format(
    self,
    width: int,
    height: int,
    x_offset: Optional[int] = None,
    y_offset: Optional[int] = None,
  ) -> Tuple[int, int]:
    self.width = width
    self.height = height
    if x_offset is not None:
      self.x_offset = x_offset
    if y_offset is not None:
      self.y_offset = y_offset
    return width, height

  async def capture(self, flush_frames: int = 2) -> CameraFrame:
    del flush_frames
    return CameraFrame(
      data=bytes(self.width * self.height),
      width=self.width,
      height=self.height,
      bit_depth=self.bit_depth,
      exposure_ms=self.exposure_ms,
      gain=self.gain,
      captured_at=0.0,
    )


def make_linear_axis_config(**changes: Any) -> LinearAxisConfig:
  """Build a complete linear-axis config for a focused test."""
  config = LinearAxisConfig(
    motion_name="Test linear axis",
    config_version=0,
    motor_type=0,
    comm_index=0,
    controller_index=0,
    axis_index=1,
    enabled=True,
    max_velocity=0.0,
    max_acceleration=0.0,
    max_deceleration=0.0,
    max_s_acceleration=0,
    moderate_acceleration=0.0,
    minimum_acceleration=0.0,
    moderate_s_acceleration=0,
    minimum_s_acceleration=0,
    s_curve_support=False,
    home_type="",
    homing_velocity=0.0,
    index_velocity=0.0,
    homing_short_move=0,
    home_offset=0.0,
    positive_limit=False,
    negative_limit=False,
    limit_polarity=0,
    invert_axis_direction=False,
    default_positive_direction=False,
    moving_current_percentage=0,
    holding_current_percentage=0,
    loading_current_percentage=0,
    moving_overload_limit=0,
    mode_enable_limits=False,
    mode_enable_step_and_direction=False,
    mode_enable_position_correction=False,
    mode_enable_motor_slave_to_encoder=False,
    coarse_position_error_window=0,
    fine_position_error_window=0,
    gain=0,
    encoder_to_motor_tick_ratio=0.0,
    backlash_compensation=0,
    motor_response_time=0,
    min_position=0.0,
    max_position=0.0,
    mm_per_encoder_tick=0.0,
  )
  return replace(config, **changes)


def make_filter_wheel_config(**changes: Any) -> FilterWheelConfig:
  """Build a complete filter-wheel config for a focused test."""
  config = FilterWheelConfig(
    motion_name="Test filter wheel",
    config_version=0,
    motor_type=0,
    comm_index=0,
    controller_index=0,
    axis_index=1,
    enabled=True,
    max_velocity=0.0,
    max_acceleration=0.0,
    max_deceleration=0.0,
    max_s_acceleration=0,
    moderate_acceleration=0.0,
    minimum_acceleration=0.0,
    moderate_s_acceleration=0,
    minimum_s_acceleration=0,
    s_curve_support=False,
    home_type="",
    homing_velocity=0.0,
    index_velocity=0.0,
    homing_short_move=0,
    home_offset=0.0,
    positive_limit=False,
    negative_limit=False,
    limit_polarity=0,
    invert_axis_direction=False,
    default_positive_direction=False,
    moving_current_percentage=0,
    holding_current_percentage=0,
    loading_current_percentage=0,
    moving_overload_limit=0,
    mode_enable_limits=False,
    mode_enable_step_and_direction=False,
    mode_enable_position_correction=False,
    mode_enable_motor_slave_to_encoder=False,
    coarse_position_error_window=0,
    fine_position_error_window=0,
    gain=0,
    encoder_to_motor_tick_ratio=0.0,
    backlash_compensation=0,
    motor_response_time=0,
    encoder_ticks_per_revolution=0,
    number_of_filters=0,
    filter_map=[],
  )
  return replace(config, **changes)


def make_galvo_config(**changes: Any) -> GalvoConfig:
  """Build a complete galvo config for a focused test."""
  config = GalvoConfig(
    config_version=0,
    controller_index=0,
    position_error_window=0,
    velocity_error_window=0,
    big_move_delay=0.0,
    min_voltage=0.0,
    max_voltage=0.0,
    invert_voltage=False,
    enabled=True,
  )
  return replace(config, **changes)


def make_calibration_config(**changes: Any) -> CalibrationConfig:
  """Build a complete optical/stage calibration for a focused test."""
  config = CalibrationConfig(
    microns_per_pixel_x=1.0,
    microns_per_pixel_y=1.0,
    image_width_pixels=2048,
    image_height_pixels=2048,
    image_to_stage_theta_radians=0.0,
    galvo_to_stage_theta_radians=0.0,
    calibrated_plate_corner_x=0.0,
    calibrated_plate_corner_y=0.0,
    calibrated_plate_to_stage_theta_radians=0.0,
    stage_x_scale=1.0,
    stage_y_scale=1.0,
    stage_shear=0.0,
    stage_x_shear_offset=0.0,
    stage_y_shear_offset=0.0,
    calibrated_z_position=0.0,
    calibrated_z_glass_plate_delta=0.0,
    z_plane_x_coeff=0.0,
    z_plane_y_coeff=0.0,
  )
  return replace(config, **changes)


def make_hardware_default_config(**changes: Any) -> HardwareDefaultConfig:
  """Build a complete hardware-default calibration for a focused test."""
  config = HardwareDefaultConfig(
    default_calibrated_z=0.0,
    default_plate_x_corner_stage_coordinate=0.0,
    default_plate_y_corner_stage_coordinate=0.0,
    default_x_field_of_view_mm=0.0,
    default_y_field_of_view_mm=0.0,
    default_x_galvo_mm_per_volt=0.0,
    default_y_galvo_mm_per_volt=0.0,
  )
  return replace(config, **changes)


def make_navigation_config(**changes: Any) -> NavigationConfig:
  """Build a complete navigation config for a focused test."""
  config = NavigationConfig(
    frame_overlap_x_mm=0.0,
    frame_overlap_y_mm=0.0,
    max_galvo_deflection_x_mm=0.0,
    max_galvo_deflection_y_mm=0.0,
  )
  return replace(config, **changes)


def make_test_config() -> CeligoConfig:
  """Build a complete configuration whose individual hardware components are absent."""
  magnifications = {
    value: GalvoMagnificationCalibration(center_voltage=0.0, frame_size_volts=0.0)
    for value in (3, 5, 10, 20)
  }
  optical_axis = GalvoAxisOpticalCalibration(
    magnifications=magnifications,
    logical_filter_offsets={},
    laser_center_voltage=0.0,
    uv_laser_center_voltage=0.0,
  )
  return CeligoConfig(
    hardware=CeligoHardwareConfig(),
    channel_descriptors=[],
    channels_by_magnification={magnification: {} for magnification in (3, 5, 10, 20)},
    calibration=make_calibration_config(),
    hardware_defaults=make_hardware_default_config(),
    galvo_calibrations={},
    galvo_optical_calibration=GalvoOpticalCalibration(
      x=optical_axis,
      y=optical_axis,
    ),
    navigation=make_navigation_config(),
  )


def make_celigo(
  *,
  config: Optional[CeligoConfig] = None,
  hardware: Optional[CeligoHardwareConfig] = None,
  allow_laser: bool = False,
) -> Celigo:
  """Construct a hardware-free ``Celigo`` for tests."""
  config = make_test_config() if config is None else config
  if hardware is not None:
    config.hardware = hardware
  camera = FakeCamera()
  with (
    patch("pylabrobot.revvity.celigo.celigo.FTDI", return_value=object()),
    patch("pylabrobot.revvity.celigo.celigo.LumeneraCamera", return_value=camera),
  ):
    return Celigo(config=config, allow_laser=allow_laser)
