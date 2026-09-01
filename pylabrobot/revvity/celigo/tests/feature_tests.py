"""Tests for Celigo startup, camera, focus, and laser safety."""

import asyncio
import ctypes
import struct
import threading
import unittest
import zlib
from unittest.mock import AsyncMock, patch

from pylabrobot.revvity.celigo.camera import CameraError, CameraFrame, LumeneraCamera
from pylabrobot.revvity.celigo.celigo import (
  CeligoError,
  ControllerInfo,
  ControllerStatus,
  DetectedMotorAddress,
)
from pylabrobot.revvity.celigo.config import (
  CeligoHardwareConfig,
  DigitalIOConfig,
  ExternalCameraControlConfig,
  FilterWheelConfig,
  GalvoAxisOpticalCalibration,
  GalvoMagnificationCalibration,
  GalvoOpticalCalibration,
  IlluminationChannelConfig,
  IOConfig,
  LightingIOConfig,
)
from pylabrobot.revvity.celigo.galvo import (
  _CMD_CALIBRATE_GALVO,
  _CMD_MOVE_GALVO,
  GalvoControllerStatus,
  dac_count_to_volts,
)
from pylabrobot.revvity.celigo.laser import (
  _CMD_FIRE_GALVO_GRID,
  _CMD_FIRE_LASER,
  _CMD_LOAD_FIRING_TABLE,
  _CMD_READ_LASER_COMM,
  _CMD_SEND_LASER_COMM,
  _CMD_TARGETED_FIRE,
  Laser,
)
from pylabrobot.revvity.celigo.motion import (
  _LIMIT_OPTO_1,
  Axis,
  _parse_motor_controller_firmware_version,
)
from pylabrobot.revvity.celigo.tests.helpers import (
  make_calibration_config,
  make_celigo,
  make_filter_wheel_config,
  make_galvo_config,
  make_linear_axis_config,
)


def _filter_config() -> FilterWheelConfig:
  return make_filter_wheel_config(
    motion_name="Dichroic",
    axis_index=4,
    enabled=True,
    home_type="Filter_Accurate",
    home_offset=20,
    index_velocity=600,
    homing_velocity=5000,
    max_velocity=30000,
    max_acceleration=5000,
    moving_current_percentage=80,
    holding_current_percentage=30,
    default_positive_direction=True,
    limit_polarity=1,
    mode_enable_position_correction=True,
    encoder_to_motor_tick_ratio=25.6,
    moving_overload_limit=10,
    coarse_position_error_window=20,
    fine_position_error_window=1,
    gain=5,
    motor_response_time=2,
    encoder_ticks_per_revolution=8000,
    number_of_filters=4,
    filter_map=[],
  )


def _galvo_controller_status(
  *,
  fire_table_size: int,
  points_loaded: int,
  fire_table_index: int,
) -> GalvoControllerStatus:
  return GalvoControllerStatus(
    x_busy=False,
    y_busy=False,
    x_hardware_voltage=0.0,
    y_hardware_voltage=0.0,
    fire_table_size=fire_table_size,
    points_loaded=points_loaded,
    fire_table_index=fire_table_index,
    firing_status=0,
    capture_armed=False,
    capture_table_size=0,
  )


class TestMotorStartup(unittest.IsolatedAsyncioTestCase):
  async def test_hardware_initialization_requires_galvos_as_a_pair(self):
    celigo = make_celigo()
    celigo.config.hardware.x_galvo = make_galvo_config()

    async def no_op():
      return None

    async def controller_info():
      return ControllerInfo(device_index=0, firmware_version=(1, 3, 0), uart_buffer_length=64)

    async def detected_motor_addresses():
      return []

    with (
      patch.multiple(
        celigo,
        abort_controller_operation=no_op,
        request_controller_info=controller_info,
        request_detected_motor_addresses=detected_motor_addresses,
        _initialize_safe_outputs=no_op,
      ),
      self.assertRaisesRegex(CeligoError, "both be enabled or both be absent"),
    ):
      await celigo._initialize_hardware()

  async def test_hardware_initialization_centers_galvos_for_active_magnification(self):
    celigo = make_celigo()
    celigo.config.magnification = 5
    celigo.config.hardware.x_galvo = make_galvo_config()
    celigo.config.hardware.y_galvo = make_galvo_config()
    centered_magnifications = []

    async def no_op(*_args, **_kwargs):
      return None

    async def controller_info():
      return ControllerInfo(device_index=0, firmware_version=(1, 3, 0), uart_buffer_length=64)

    async def detected_motor_addresses():
      return []

    async def calibrate(*_args, **_kwargs):
      return True

    async def home(*, magnification, logical_filter=None):
      del logical_filter
      centered_magnifications.append(magnification)
      return 0.0, 0.0

    with (
      patch.multiple(
        celigo,
        abort_controller_operation=no_op,
        request_controller_info=controller_info,
        request_detected_motor_addresses=detected_motor_addresses,
        _initialize_safe_outputs=no_op,
      ),
      patch.multiple(
        celigo.galvo,
        _set_settling_window=no_op,
        calibrate=calibrate,
        home=home,
      ),
    ):
      await celigo._initialize_hardware()

    self.assertEqual(centered_magnifications, [5])

  async def test_home_imaging_axes_synchronizes_magnification_with_z_retracted(self):
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(axis_index=1),
        y_axis=make_linear_axis_config(axis_index=2),
        z_axis=make_linear_axis_config(axis_index=3),
        dichroic_filter_wheel=make_filter_wheel_config(axis_index=4),
        magnification_changer=make_filter_wheel_config(axis_index=5),
      )
    )
    celigo.config.magnification = 5
    operations = []

    async def track(operation):
      operations.append(operation)

    async def move_magnification(magnification):
      operations.append(f"magnification_move_{magnification}")
      return 0

    with (
      patch.object(celigo, "turn_off_illumination", lambda: track("illumination_off")),
      patch.object(celigo.z_axis, "home", lambda: track("z_home")),
      patch.multiple(
        celigo.magnification_changer,
        home=lambda: track("magnification_home"),
        move_to=move_magnification,
      ),
      patch.object(celigo.x_axis, "home", lambda: track("x_home")),
      patch.object(celigo.y_axis, "home", lambda: track("y_home")),
      patch.object(celigo.dichroic_filter, "home", lambda: track("dichroic_home")),
    ):
      await celigo.home_imaging_axes()

    self.assertEqual(
      operations,
      [
        "illumination_off",
        "z_home",
        "magnification_home",
        "magnification_move_5",
        "x_home",
        "y_home",
        "dichroic_home",
      ],
    )

  async def test_hardware_initialization_rejects_an_undetected_configured_motor(self):
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          motion_name="X Axis",
          axis_index=1,
        )
      )
    )

    async def no_op():
      return None

    async def controller_info():
      return ControllerInfo(device_index=0, firmware_version=(1, 3, 0), uart_buffer_length=64)

    async def detected_motor_addresses():
      return [DetectedMotorAddress(uart_index=0, motor_index=2)]

    with (
      patch.multiple(
        celigo,
        abort_controller_operation=no_op,
        request_controller_info=controller_info,
        request_detected_motor_addresses=detected_motor_addresses,
      ),
      self.assertRaisesRegex(CeligoError, r"X Axis \(1\)"),
    ):
      await celigo._initialize_hardware()

  def test_duplicate_enabled_motor_addresses_are_rejected(self):
    with self.assertRaisesRegex(CeligoError, "share motor address 1"):
      make_celigo(
        hardware=CeligoHardwareConfig(
          x_axis=make_linear_axis_config(motion_name="X Axis", axis_index=1),
          y_axis=make_linear_axis_config(motion_name="Y Axis", axis_index=1),
        )
      )

  def test_motion_profile_rejects_invalid_configured_rates(self):
    axis_config = _filter_config()
    axis_config.max_acceleration = 0
    celigo = make_celigo(hardware=CeligoHardwareConfig(dichroic_filter_wheel=axis_config))

    with self.assertRaisesRegex(CeligoError, "invalid configured rate 0"):
      celigo.dichroic_filter._motion_profile()

  def test_motor_firmware_version_is_structured_not_float(self):
    self.assertEqual(
      _parse_motor_controller_firmware_version("EZStepper Controller V7.21"),
      (7, 21),
    )
    self.assertLess(
      _parse_motor_controller_firmware_version("EZStepper Controller V7.9"),
      (7, 12),
    )
    with self.assertRaisesRegex(CeligoError, "Could not parse"):
      _parse_motor_controller_firmware_version("EZStepper Controller unknown")

  async def test_safe_output_initialization_zeros_all_vendor_outputs(self):
    celigo = make_celigo()
    analog = []
    digital = []

    async def dac(channel, value):
      analog.append((channel, value))

    async def output(bit, on):
      digital.append((bit, on))

    with patch.multiple(
      celigo,
      set_analog_output_count=dac,
      set_digital_output=output,
    ):
      await celigo._initialize_safe_outputs()
    self.assertEqual(analog, [(0, 0), (1, 0), (2, 0), (3, 0)])
    self.assertEqual(digital, [(bit, False) for bit in range(12)])

  async def test_safe_output_initialization_respects_inverted_outputs_and_delay(self):
    inverted_light = LightingIOConfig(
      config_version=1000,
      controller_index=0,
      channel=2,
      enabled=True,
      invert=True,
      io_name="eFluorescentIntensity",
      min_voltage=0,
      max_voltage=10,
      delay=0.025,
    )
    inverted_lamp_power = DigitalIOConfig(
      config_version=1000,
      io_type="Out",
      bit_index=7,
      invert=True,
      enabled=True,
      io_name="ExcitationLampPower",
    )
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        io=IOConfig(
          analog_ins=[],
          digital_ios=[inverted_lamp_power],
          lighting_ios=[inverted_light],
        )
      )
    )
    analog = []
    digital = []

    async def set_analog(channel, value):
      analog.append((channel, value))

    async def set_digital(bit, high):
      digital.append((bit, high))

    with (
      patch.multiple(
        celigo,
        set_analog_output_count=set_analog,
        set_digital_output=set_digital,
      ),
      patch("pylabrobot.revvity.celigo.celigo.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
      await celigo._initialize_safe_outputs()

    self.assertEqual(analog, [(0, 0), (1, 0), (2, 4095), (3, 0)])
    self.assertEqual(digital, [(bit, bit == 7) for bit in range(12)])
    sleep.assert_awaited_once_with(0.025)
    self.assertIsNone(celigo._fluorescence_on_since)

  async def test_initialization_replays_vendor_tokens(self):
    celigo = make_celigo(hardware=CeligoHardwareConfig(dichroic_filter_wheel=_filter_config()))
    commands = []

    async def motor_query(command):
      commands.append(command)
      data = "EZStepper Controller V7.21" if command.endswith("&\r") else ""
      return f"/0`{data}"

    with patch.object(celigo.motor_controller, "send_command", motor_query):
      await celigo.dichroic_filter._initialize()
    self.assertEqual(commands[0], "/4&\r")
    self.assertEqual(commands[1], "/4T\r")
    self.assertEqual(commands[2], "/4N32R\r")
    self.assertEqual(
      commands[3],
      "/4F0f1m80h30aE25600au10aC20ac1x5V30000L5000aP2R\r",
    )
    self.assertEqual(commands[4], "/4n0R\r")

  async def test_failed_reinitialization_clears_axis_state(self):
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          max_velocity=1,
          max_acceleration=1,
        )
      )
    )
    celigo.x_axis._initialized = True
    celigo.x_axis._supports_accurate_encoder_index = True

    async def fail_firmware_query():
      raise CeligoError("firmware query failed")

    with (
      patch.object(
        celigo.x_axis.motor,
        "request_motor_controller_firmware_version",
        fail_firmware_query,
      ),
      self.assertRaisesRegex(CeligoError, "firmware query failed"),
    ):
      await celigo.x_axis._initialize()
    self.assertFalse(celigo.x_axis.is_initialized)
    self.assertFalse(celigo.x_axis._supports_accurate_encoder_index)

  async def test_illumination_shutdown_does_not_require_a_strobe_output(self):
    lighting_outputs = [
      LightingIOConfig(
        config_version=1000,
        controller_index=0,
        channel=0,
        enabled=True,
        invert=False,
        io_name="brightfield",
        min_voltage=0,
        max_voltage=10,
        delay=0.0,
      ),
      LightingIOConfig(
        config_version=1000,
        controller_index=0,
        channel=2,
        enabled=True,
        invert=False,
        io_name="fluorescence",
        min_voltage=0,
        max_voltage=10,
        delay=0.0,
      ),
    ]
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        io=IOConfig(
          analog_ins=[],
          digital_ios=[],
          lighting_ios=lighting_outputs,
        )
      )
    )
    analog_writes = []

    async def write_analog(channel, value):
      analog_writes.append((channel, value))

    with patch.object(celigo, "set_analog_output_count", write_analog):
      await celigo.turn_off_illumination()
    self.assertEqual(analog_writes, [(0, 0), (2, 0)])

  async def test_illumination_shutdown_zeros_analog_outputs_after_strobe_failure(self):
    strobe = DigitalIOConfig(
      config_version=1000,
      io_type="Out",
      bit_index=6,
      invert=False,
      enabled=True,
      io_name="FLOnOff",
    )
    lighting = LightingIOConfig(
      config_version=1000,
      controller_index=0,
      channel=2,
      enabled=True,
      invert=False,
      io_name="fluorescence",
      min_voltage=0,
      max_voltage=10,
      delay=0.0,
    )
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        io=IOConfig(
          analog_ins=[],
          digital_ios=[strobe],
          lighting_ios=[lighting],
        )
      )
    )
    analog_writes = []

    async def fail_strobe(_bit_index, _enabled):
      raise CeligoError("strobe write failed")

    async def write_analog(channel, value):
      analog_writes.append((channel, value))

    with (
      patch.multiple(
        celigo,
        set_digital_output=fail_strobe,
        set_analog_output_count=write_analog,
      ),
      self.assertRaisesRegex(CeligoError, "strobe write failed"),
    ):
      await celigo.turn_off_illumination()
    self.assertEqual(analog_writes, [(2, 0)])

  async def test_configured_move_restores_hold_current_after_failure(self):
    celigo = make_celigo(hardware=CeligoHardwareConfig(dichroic_filter_wheel=_filter_config()))
    commands = []

    async def send(command):
      commands.append(command)
      return "/0`"

    async def fail_wait(*_args, **_kwargs):
      raise TimeoutError("simulated timeout")

    with (
      patch.object(celigo.motor_controller, "send_command", send),
      patch.object(celigo.dichroic_filter.motor, "wait_until_ready", fail_wait),
      self.assertRaises(TimeoutError),
    ):
      await celigo.dichroic_filter.move_to_ticks(2000)

    self.assertEqual(commands.count("/4T\r"), 3)
    self.assertEqual(commands[-1], "/4h30R\r")

  async def test_cancelled_move_terminates_and_restores_hold_current(self):
    celigo = make_celigo(hardware=CeligoHardwareConfig(dichroic_filter_wheel=_filter_config()))
    wait_started = asyncio.Event()
    cleanup_operations = []

    class SuccessfulResponse:
      ok = True
      error_code = 0

    async def send(*_args, **_kwargs):
      return SuccessfulResponse()

    async def wait_until_ready(*_args, **_kwargs):
      wait_started.set()
      await asyncio.Future()

    async def terminate():
      cleanup_operations.append("terminate")

    async def set_parameter(token, value, _description):
      cleanup_operations.append(f"{token}{value}")

    with patch.multiple(
      celigo.dichroic_filter.motor,
      send_command=send,
      wait_until_ready=wait_until_ready,
      _terminate=terminate,
      _set_parameter=set_parameter,
    ):
      move = asyncio.create_task(celigo.dichroic_filter.move_to_ticks(2000))
      await wait_started.wait()
      move.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await move

    self.assertEqual(cleanup_operations, ["terminate", "h30"])

  async def test_filter_move_uses_fine_window_and_retries(self):
    celigo = make_celigo(hardware=CeligoHardwareConfig(dichroic_filter_wheel=_filter_config()))
    waits = iter((102, 100))
    wait_count = 0

    async def send(_command):
      return "/0`"

    async def wait(*_args, **_kwargs):
      nonlocal wait_count
      wait_count += 1
      return next(waits)

    with (
      patch.object(celigo.motor_controller, "send_command", send),
      patch.object(celigo.dichroic_filter.motor, "wait_until_ready", wait),
    ):
      self.assertEqual(await celigo.dichroic_filter.move_to_ticks(100), 100)
    self.assertEqual(wait_count, 2)

  async def test_normal_accurate_home_checks_limit_indexes_and_moves_to_minimum(self):
    axis = make_linear_axis_config(
      motion_name="X Axis",
      axis_index=1,
      enabled=True,
      home_type="Normal_Accurate",
      homing_velocity=15,
      index_velocity=3,
      homing_short_move=2000,
      home_offset=-18,
      min_position=3,
      max_position=125,
      mm_per_encoder_tick=0.0127,
      max_velocity=45,
      max_acceleration=45,
      negative_limit=True,
      mode_enable_limits=True,
      mode_enable_position_correction=True,
      s_curve_support=True,
      fine_position_error_window=1,
    )
    celigo = make_celigo(hardware=CeligoHardwareConfig(x_axis=axis))
    celigo.move_timeout = 30.0
    celigo.x_axis._initialized = True
    celigo.x_axis._supports_accurate_encoder_index = True
    encoder_positions = iter((100, 105))
    flags = iter((_LIMIT_OPTO_1, 0))
    relative_moves = []
    index_homes = []
    absolute_moves = []

    async def encoder():
      return next(encoder_positions)

    async def relative(positive, distance, velocity):
      relative_moves.append((positive, distance, velocity))
      return 0

    async def get_flags():
      return next(flags)

    async def no_op(*_args, **_kwargs):
      return None

    async def index_home(distance, velocity, mode, **_kwargs):
      index_homes.append((distance, velocity, mode))
      return 0

    async def absolute(_axis, target, **_kwargs):
      absolute_moves.append(target)
      return target

    with (
      patch.multiple(
        celigo.x_axis,
        request_encoder_ticks=encoder,
        _move_homing_relative_ticks=relative,
        request_limit_flags=get_flags,
        _restore_homing_configuration=no_op,
        _home_to_encoder_index=index_home,
      ),
      patch.multiple(celigo.x_axis.motor, _set_mode=no_op, _set_parameter=no_op),
      patch.object(Axis, "move_to_ticks", new=absolute),
    ):
      self.assertEqual(await celigo.x_axis.home(), -1181)
    self.assertEqual(
      relative_moves,
      [(True, 5, 3543), (False, 25000, 1181), (True, 2000, 1181)],
    )
    self.assertEqual(index_homes, [(4000, 236, 6)])
    self.assertEqual(absolute_moves, [0, -1181])
    self.assertTrue(celigo.x_axis.has_position_reference)

  async def test_z_home_uses_no_index_mode_and_a_worst_case_timeout(self):
    axis = make_linear_axis_config(
      motion_name="Z Axis",
      axis_index=3,
      enabled=True,
      home_type="NormalWithHardstopCheck",
      homing_velocity=4,
      index_velocity=0.15,
      homing_short_move=2000,
      home_offset=0.05,
      min_position=0,
      max_position=6.5,
      mm_per_encoder_tick=0.000396875,
      max_velocity=4,
      max_acceleration=4,
      negative_limit=True,
      mode_enable_limits=True,
      mode_enable_position_correction=True,
    )
    celigo = make_celigo(hardware=CeligoHardwareConfig(z_axis=axis))
    celigo.move_timeout = 30.0
    celigo.z_axis._initialized = True
    celigo.z_axis._supports_accurate_encoder_index = True
    encoders = iter((100, 105))
    flags = iter((_LIMIT_OPTO_1, 0))
    index_home = []

    async def no_op(*_args, **_kwargs):
      return None

    async def encoder():
      return next(encoders)

    async def relative(*_args, **_kwargs):
      return 0

    async def get_flags():
      return next(flags)

    async def home_index(distance, velocity, mode, **kwargs):
      index_home.append((distance, velocity, mode, kwargs["timeout"]))
      return 0

    async def absolute(_axis, target, **_kwargs):
      return target

    with (
      patch.multiple(celigo.z_axis.motor, _set_mode=no_op, _set_parameter=no_op),
      patch.multiple(
        celigo.z_axis,
        _restore_homing_configuration=no_op,
        request_encoder_ticks=encoder,
        _move_homing_relative_ticks=relative,
        request_limit_flags=get_flags,
        _home_to_encoder_index=home_index,
      ),
      patch.object(Axis, "move_to_ticks", new=absolute),
    ):
      self.assertEqual(await celigo.z_axis.home(), 126)
    self.assertEqual(index_home, [(25000, 378, 1, 68.13756613756614)])
    self.assertTrue(celigo.z_axis.has_position_reference)

  async def test_home_fails_closed_when_negative_limit_does_not_activate(self):
    axis = make_linear_axis_config(
      motion_name="X Axis",
      axis_index=1,
      enabled=True,
      home_type="Normal_Accurate",
      homing_velocity=15,
      index_velocity=3,
      homing_short_move=2000,
      home_offset=-18,
      min_position=3,
      max_position=125,
      mm_per_encoder_tick=0.0127,
      max_velocity=45,
      max_acceleration=45,
      negative_limit=True,
      mode_enable_limits=True,
    )
    celigo = make_celigo(hardware=CeligoHardwareConfig(x_axis=axis))
    celigo.move_timeout = 30.0
    celigo.x_axis._initialized = True
    celigo.x_axis._supports_accurate_encoder_index = True
    encoders = iter((100, 105))
    commands = []
    restored = 0

    async def encoder():
      return next(encoders)

    async def relative(*_args, **_kwargs):
      return 0

    async def get_flags():
      return 0

    async def no_op(*_args, **_kwargs):
      return None

    async def restore():
      nonlocal restored
      restored += 1

    async def terminate():
      commands.append((1, "T", False))

    with (
      patch.multiple(
        celigo.x_axis,
        request_encoder_ticks=encoder,
        _move_homing_relative_ticks=relative,
        request_limit_flags=get_flags,
        _restore_homing_configuration=restore,
      ),
      patch.multiple(
        celigo.x_axis.motor,
        _set_mode=no_op,
        _set_parameter=no_op,
        _terminate=terminate,
      ),
      self.assertRaisesRegex(CeligoError, "without activating"),
    ):
      await celigo.x_axis.home()
    self.assertFalse(celigo.x_axis.has_position_reference)
    self.assertEqual(commands[-1], (1, "T", False))
    self.assertEqual(restored, 1)


class TestAccurateFilterHome(unittest.IsolatedAsyncioTestCase):
  async def test_index_timeout_terminates_and_restores_configured_mode(self):
    celigo = make_celigo(hardware=CeligoHardwareConfig(dichroic_filter_wheel=_filter_config()))
    commands = []

    async def send(command):
      commands.append(command)
      return "/0`"

    async def timeout(*_args, **_kwargs):
      raise TimeoutError("simulated index timeout")

    with (
      patch.object(celigo.motor_controller, "send_command", send),
      patch.object(celigo.dichroic_filter.motor, "wait_until_ready", timeout),
      self.assertRaises(TimeoutError),
    ):
      await celigo.dichroic_filter._home_to_encoder_index(
        2400,
        600,
        6,
        timeout=5,
      )
    self.assertIn("/4T\r", commands)
    self.assertTrue(commands[-1].startswith("/4n"))

  async def test_scans_physical_positions_until_opto(self):
    celigo = make_celigo(hardware=CeligoHardwareConfig(dichroic_filter_wheel=_filter_config()))
    celigo.move_timeout = 30.0
    celigo.dichroic_filter._initialized = True
    celigo.dichroic_filter._supports_accurate_encoder_index = True
    moves = []
    flags = iter((0, 0, _LIMIT_OPTO_1))

    async def home_index(*_args, **_kwargs):
      return 0

    async def set_mode(*_args, **_kwargs):
      return None

    async def move_axis(_axis, target, velocity_ticks_per_second=None, **_kwargs):
      del velocity_ticks_per_second
      moves.append(target)
      return target

    async def get_flags():
      return next(flags)

    with (
      patch.multiple(
        celigo.dichroic_filter,
        _home_to_encoder_index=home_index,
        request_limit_flags=get_flags,
      ),
      patch.object(celigo.dichroic_filter.motor, "_set_mode", set_mode),
      patch.object(Axis, "move_to_ticks", new=move_axis),
    ):
      self.assertEqual(await celigo.dichroic_filter.home(), 4020)
    self.assertEqual(moves, [20, 2020, 4020])
    self.assertTrue(celigo.dichroic_filter.has_position_reference)


class TestCameraFrame(unittest.TestCase):
  def test_statistics_and_focus_metric(self):
    flat = CameraFrame(bytes([5] * 25), 5, 5, 8, 1.0, 0.0, 0.0)
    sharp_data = bytearray([5] * 25)
    sharp_data[12] = 250
    sharp = CameraFrame(bytes(sharp_data), 5, 5, 8, 1.0, 0.0, 0.0)
    self.assertEqual(flat.statistics(), (5, 5, 5.0))
    self.assertEqual(flat.sharpness(sample_step=1), 0.0)
    self.assertGreater(sharp.sharpness(sample_step=1), 0.0)

  def test_dependency_free_png_encoding(self):
    frame = CameraFrame(bytes((0, 127, 255, 64)), 2, 2, 8, 1.0, 0.0, 0.0)
    encoded = frame.to_png_bytes()

    self.assertEqual(encoded[:8], b"\x89PNG\r\n\x1a\n")
    ihdr_length = struct.unpack(">I", encoded[8:12])[0]
    self.assertEqual(encoded[12:16], b"IHDR")
    self.assertEqual(
      struct.unpack(">IIBBBBB", encoded[16 : 16 + ihdr_length]), (2, 2, 8, 0, 0, 0, 0)
    )
    idat_offset = 16 + ihdr_length + 4
    idat_length = struct.unpack(">I", encoded[idat_offset : idat_offset + 4])[0]
    self.assertEqual(encoded[idat_offset + 4 : idat_offset + 8], b"IDAT")
    compressed = encoded[idat_offset + 8 : idat_offset + 8 + idat_length]
    self.assertEqual(zlib.decompress(compressed), b"\x00\x00\x7f\x00\xff\x40")

    thumbnail = CameraFrame(bytes(range(24)), 6, 4, 8, 1.0, 0.0, 0.0).to_png_bytes(maximum_size=3)
    self.assertEqual(struct.unpack(">II", thumbnail[16:24]), (3, 2))


class _FakeLucamLibrary:
  def __init__(self):
    self.exposure = 5.0
    self.gain = 1.0
    self.closed = False
    self.frame_format = {
      "x_offset": 0,
      "y_offset": 0,
      "width": 4,
      "height": 3,
      "pixel_format": 0,
      "subsample_x": 1,
      "flags_x": 0,
      "subsample_y": 1,
      "flags_y": 0,
    }
    self.stream_operations = []

  def __getitem__(self, name):
    return {
      "LucamCameraOpen": self.LucamCameraOpen,
      "LucamCameraClose": self.LucamCameraClose,
      "LucamStreamVideoControl": self.LucamStreamVideoControl,
      "LucamGetFormat": self.LucamGetFormat,
      "LucamSetFormat": self.LucamSetFormat,
      "LucamTakeVideo": self.LucamTakeVideo,
      "LucamGetProperty": self.LucamGetProperty,
      "LucamSetProperty": self.LucamSetProperty,
      "LucamGetLastErrorForCamera": self.LucamGetLastErrorForCamera,
    }[name]

  def LucamCameraOpen(self, _index):
    return 1

  def LucamCameraClose(self, _handle):
    self.closed = True
    return 1

  def LucamStreamVideoControl(self, _handle, _operation, _unused):
    self.stream_operations.append(_operation)
    return 1

  def LucamGetFormat(self, _handle, frame_format, frame_rate):
    frame_format._obj.x_offset = self.frame_format["x_offset"]
    frame_format._obj.y_offset = self.frame_format["y_offset"]
    frame_format._obj.width = self.frame_format["width"]
    frame_format._obj.height = self.frame_format["height"]
    frame_format._obj.pixel_format = self.frame_format["pixel_format"]
    frame_format._obj.subsample_x = self.frame_format["subsample_x"]
    frame_format._obj.flags_x = self.frame_format["flags_x"]
    frame_format._obj.subsample_y = self.frame_format["subsample_y"]
    frame_format._obj.flags_y = self.frame_format["flags_y"]
    frame_rate._obj.value = 10.0
    return 1

  def LucamSetFormat(self, _handle, frame_format, _frame_rate):
    self.frame_format = {
      "x_offset": frame_format._obj.x_offset,
      "y_offset": frame_format._obj.y_offset,
      "width": frame_format._obj.width,
      "height": frame_format._obj.height,
      "pixel_format": frame_format._obj.pixel_format,
      "subsample_x": frame_format._obj.subsample_x,
      "flags_x": frame_format._obj.flags_x,
      "subsample_y": frame_format._obj.subsample_y,
      "flags_y": frame_format._obj.flags_y,
    }
    return 1

  def LucamTakeVideo(self, _handle, _count, buffer):
    ctypes.memset(buffer, 7, 12)
    return 1

  def LucamGetProperty(self, _handle, property_id, value, _flags):
    value._obj.value = self.exposure if property_id == 20 else self.gain
    return 1

  def LucamSetProperty(self, _handle, property_id, value, _flags):
    if property_id == 20:
      self.exposure = value.value
    else:
      self.gain = value.value
    return 1

  def LucamGetLastErrorForCamera(self, _handle):
    return 0


class _SignatureRecordingFunction:
  def __init__(self, function):
    self._function = function
    self.restype = None
    self.argtypes = None

  def __call__(self, *args):
    return self._function(*args)


class TestLumeneraCamera(unittest.IsolatedAsyncioTestCase):
  def test_sdk_signature_is_bound_to_the_called_attribute(self):
    library = _FakeLucamLibrary()
    camera_open = _SignatureRecordingFunction(library.LucamCameraOpen)
    camera = LumeneraCamera(library=library)

    with patch.object(library, "LucamCameraOpen", camera_open):
      camera._load_library()

    self.assertIs(camera_open.restype, ctypes.c_void_p)
    self.assertEqual(camera_open.argtypes, [ctypes.c_uint32])

  async def test_centered_roi_is_set_read_back_and_stream_restarted(self):
    library = _FakeLucamLibrary()
    camera = LumeneraCamera(library=library)
    await camera.setup()
    self.assertEqual(await camera.set_frame_format(2, 1), (2, 1))
    self.assertEqual((camera.x_offset, camera.y_offset), (1, 1))
    self.assertEqual(library.stream_operations, [1, 0, 1])
    await camera.stop()

  async def test_sdk_capture_is_packaged(self):
    library = _FakeLucamLibrary()
    camera = LumeneraCamera(library=library)
    await camera.setup()
    await camera.set_exposure(2.5)
    frame = await camera.capture(flush_frames=0)
    self.assertEqual((frame.width, frame.height, frame.bit_depth), (4, 3, 8))
    self.assertEqual(frame.data, bytes([7] * 12))
    self.assertAlmostEqual(frame.exposure_ms, 2.5)
    await camera.stop()
    self.assertTrue(library.closed)
    self.assertIsNone(camera._executor)

  async def test_capture_sleeps_only_between_flushed_frames(self):
    library = _FakeLucamLibrary()
    camera = LumeneraCamera(library=library)
    await camera.setup()
    with patch("pylabrobot.revvity.celigo.camera.time.sleep") as sleep:
      await camera.capture(flush_frames=2)
    self.assertEqual(sleep.call_count, 2)
    await camera.stop()

  async def test_unsupported_pixel_format_closes_camera(self):
    library = _FakeLucamLibrary()

    def unsupported(_handle, frame_format, frame_rate):
      frame_format._obj.width = 4
      frame_format._obj.height = 3
      frame_format._obj.pixel_format = 99
      frame_rate._obj.value = 10.0
      return 1

    camera = LumeneraCamera(library=library)
    with (
      patch.object(library, "LucamGetFormat", unsupported),
      self.assertRaisesRegex(Exception, "Unsupported Lumenera pixel format"),
    ):
      await camera.setup()
    self.assertTrue(library.closed)
    self.assertFalse(camera.is_open)
    self.assertIsNone(camera._executor)

  async def test_timed_out_setup_is_poisoned_and_deferred_close_cannot_reopen(self):
    started = threading.Event()
    release = threading.Event()
    library = _FakeLucamLibrary()

    def blocking_open(_index):
      started.set()
      release.wait()
      return 1

    camera = LumeneraCamera(library=library, sdk_call_timeout=0.01)
    with (
      patch.object(library, "LucamCameraOpen", blocking_open),
      self.assertRaisesRegex(CameraError, "close is queued"),
    ):
      await camera.setup()
    self.assertTrue(started.is_set())
    self.assertFalse(camera.is_open)
    with self.assertRaisesRegex(CameraError, "poisoned"):
      await camera.stop()
    release.set()
    for _ in range(100):
      cleanup = camera._pending_cleanup
      if cleanup is not None and cleanup.done():
        break
      await asyncio.sleep(0.001)
    self.assertTrue(library.closed)
    self.assertFalse(camera.is_open)
    self.assertIsNone(camera._executor)


class TestGalvoReliability(unittest.IsolatedAsyncioTestCase):
  async def test_calibration_always_waits_and_reports_controller_status(self):
    celigo = make_celigo()
    commands = []

    async def send_command(opcode, payload=b"", retries=3):
      del retries
      commands.append((opcode, payload))
      return struct.pack(">H", 0)

    with patch.object(celigo, "send_command", send_command):
      self.assertTrue(await celigo.galvo.calibrate("x", timeout=0.9))
    self.assertEqual(commands[0][0], _CMD_CALIBRATE_GALVO)
    self.assertEqual(struct.unpack(">HHH", commands[0][1]), (0, 900, 1))

  async def test_center_filter_offset_inversion_and_settle_payload(self):
    celigo = make_celigo()
    celigo.config.hardware = CeligoHardwareConfig(
      x_galvo=make_galvo_config(
        enabled=True,
        min_voltage=0,
        max_voltage=10,
        invert_voltage=True,
      ),
      y_galvo=make_galvo_config(enabled=True, min_voltage=0, max_voltage=10),
    )
    celigo.config.magnification = 3
    celigo.reply_timeout = 2.0
    axis_x = GalvoAxisOpticalCalibration(
      magnifications={3: GalvoMagnificationCalibration(5.0, 6.5)},
      logical_filter_offsets={2: 0.2},
      laser_center_voltage=0.0,
      uv_laser_center_voltage=0.0,
    )
    axis_y = GalvoAxisOpticalCalibration(
      magnifications={3: GalvoMagnificationCalibration(4.9, 6.4)},
      logical_filter_offsets={2: -0.1},
      laser_center_voltage=0.0,
      uv_laser_center_voltage=0.0,
    )
    celigo.config.galvo_optical_calibration = GalvoOpticalCalibration(axis_x, axis_y)
    celigo.config.galvo_calibrations = {}
    transactions = []
    transaction_timeouts = []

    async def transact(opcode, payload=b"", retries=3, reply_timeout=None):
      del retries
      transactions.append((opcode, payload))
      transaction_timeouts.append(reply_timeout)
      return b"\x00\x00"

    with patch.object(celigo, "send_command", transact):
      targets = celigo.galvo.voltages_for_offset(2)
      self.assertAlmostEqual(targets[0], 5.2)
      self.assertAlmostEqual(targets[1], 4.8)
      raw = await celigo.galvo.move_single("x", 5.2)
      self.assertEqual(raw, -5.2)
      _index, _dac, wait, timeout = struct.unpack(">HiHH", transactions[0][1])
      self.assertEqual((wait, timeout), (1, 6000))
      self.assertEqual(transaction_timeouts, [7.0])
      self.assertEqual(celigo.reply_timeout, 2.0)
      with self.assertRaisesRegex(CeligoError, "outside configured range"):
        await celigo.galvo.move_single("x", 10.1)

  async def test_move_both_starts_both_axes_before_polling_and_applies_configured_delay(self):
    celigo = make_celigo()
    celigo.config.hardware = CeligoHardwareConfig(
      x_galvo=make_galvo_config(
        enabled=True,
        min_voltage=-10,
        max_voltage=10,
        big_move_delay=0.01,
      ),
      y_galvo=make_galvo_config(
        enabled=True,
        min_voltage=-10,
        max_voltage=10,
        big_move_delay=0.02,
      ),
    )
    transactions = []
    status_requests = 0

    async def send_command(opcode, payload=b"", **_kwargs):
      transactions.append((opcode, payload))
      return b""

    async def request_status():
      nonlocal status_requests
      status_requests += 1
      return _galvo_controller_status(
        fire_table_size=0,
        points_loaded=0,
        fire_table_index=0,
      )

    with (
      patch.object(celigo, "send_command", send_command),
      patch.object(celigo.galvo, "request_controller_status", request_status),
      patch("pylabrobot.revvity.celigo.galvo.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
      self.assertEqual(await celigo.galvo.move_both(1.0, 2.0), (1.0, 2.0))

    self.assertEqual([opcode for opcode, _ in transactions], [_CMD_MOVE_GALVO] * 2)
    self.assertEqual(
      [struct.unpack(">HiHH", payload)[::2] for _, payload in transactions],
      [(0, 0), (1, 0)],
    )
    self.assertEqual(status_requests, 1)
    sleep.assert_awaited_once_with(0.02)


class _FocusCamera:
  def __init__(self):
    self.is_open = True
    self.width = 5
    self.height = 5
    self.exposure_ms = 1.0
    self.gain = 0.0
    self.z = 0

  async def setup(self):
    self.is_open = True

  async def stop(self):
    self.is_open = False

  async def set_exposure(self, exposure_ms):
    self.exposure_ms = exposure_ms
    return exposure_ms

  async def set_gain(self, gain):
    self.gain = gain
    return gain

  async def capture(self, flush_frames=2):
    del flush_frames
    value = 255 - abs(self.z - 12)
    return CameraFrame(bytes([value] * 25), 5, 5, 8, self.exposure_ms, self.gain, 0.0)


class TestHostAutofocus(unittest.IsolatedAsyncioTestCase):
  async def test_zero_coarse_step_is_rejected_before_reading_hardware(self):
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        z_axis=make_linear_axis_config(
          axis_index=3,
          min_position=0,
          max_position=20,
          mm_per_encoder_tick=1,
        )
      )
    )
    with self.assertRaisesRegex(ValueError, "coarse_step_ticks positive"):
      await celigo.autofocus(coarse_step_ticks=0)

  async def test_finds_best_z_and_settles_there(self):
    camera = _FocusCamera()
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        z_axis=make_linear_axis_config(
          axis_index=3,
          min_position=0,
          max_position=20,
          mm_per_encoder_tick=1,
        )
      )
    )
    celigo.config.calibration = make_calibration_config(
      image_width_pixels=5,
      image_height_pixels=5,
    )

    async def request_encoder_ticks():
      return 10

    async def move_z(target_encoder_ticks):
      camera.z = target_encoder_ticks
      return target_encoder_ticks

    with (
      patch.object(celigo, "camera", camera),
      patch.multiple(
        celigo.z_axis,
        request_encoder_ticks=request_encoder_ticks,
        move_to_ticks=move_z,
      ),
    ):
      result = await celigo.autofocus(
        center_z_ticks=10,
        span_ticks=4,
        coarse_step_ticks=2,
        fine_step_ticks=1,
        evaluator=lambda frame: frame.data[0],
        settle_seconds=0,
      )
    self.assertEqual(result.z_ticks, 12)
    self.assertEqual(camera.z, 12)

  async def test_default_acquire_applies_channel_z_offset(self):
    camera = _FocusCamera()
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=0,
          max_position=3,
          mm_per_encoder_tick=1,
        ),
        y_axis=make_linear_axis_config(
          axis_index=2,
          min_position=0,
          max_position=3,
          mm_per_encoder_tick=1,
        ),
        z_axis=make_linear_axis_config(
          axis_index=3,
          min_position=0,
          max_position=3,
          mm_per_encoder_tick=0.01,
        ),
      )
    )
    celigo.config.calibration = make_calibration_config(
      calibrated_z_position=1.0,
      image_width_pixels=5,
      image_height_pixels=5,
    )
    celigo.config.channels_by_magnification[celigo.config.magnification] = {
      "brightfield": IlluminationChannelConfig(
        "brightfield",
        "Brightfield",
        1,
        None,
        0,
        "bf",
        False,
        0.0,
        1.0,
        1.0,
      ),
      "green": IlluminationChannelConfig(
        "green",
        "Green",
        2,
        1,
        0,
        "fl",
        True,
        0.1,
        1.0,
        1.0,
      ),
    }
    celigo.current_channel = "brightfield"
    moved_z = []

    async def move_to_well(_well, retract_z=False):
      self.assertTrue(retract_z)
      return 10, 20

    async def select(channel, **_kwargs):
      celigo.current_channel = channel

    async def move_z(position_mm):
      moved_z.append(position_mm)
      return position_mm

    async def move_both(_x, _y):
      return 0.0, 0.0

    async def no_op(*_args, **_kwargs):
      return None

    with (
      patch.multiple(
        celigo,
        camera=camera,
        move_to_well=move_to_well,
        select_channel=select,
        set_illumination_enabled=no_op,
        turn_off_illumination=no_op,
      ),
      patch.object(celigo.z_axis, "move_to", move_z),
      patch.object(celigo.galvo, "voltages_for_offset", return_value=(0.0, 0.0)),
      patch.object(celigo.galvo, "move_both", move_both),
    ):
      result = await celigo.acquire("A1", "green")
    self.assertEqual(moved_z, [1.1])
    self.assertEqual(result.z_mm, 1.1)

  async def test_default_acquire_uses_calibrated_z_and_channel_offset(self):
    camera = _FocusCamera()
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=0,
          max_position=3,
          mm_per_encoder_tick=1,
        ),
        y_axis=make_linear_axis_config(
          axis_index=2,
          min_position=0,
          max_position=3,
          mm_per_encoder_tick=1,
        ),
        z_axis=make_linear_axis_config(
          axis_index=3,
          min_position=0,
          max_position=3,
          mm_per_encoder_tick=0.01,
        ),
      )
    )
    celigo.config.calibration = make_calibration_config(
      calibrated_z_position=2.0,
      image_width_pixels=5,
      image_height_pixels=5,
    )
    celigo.config.channels_by_magnification[celigo.config.magnification] = {
      "green": IlluminationChannelConfig(
        "green",
        "Green",
        2,
        1,
        0,
        "fl",
        True,
        0.1,
        1.0,
        1.0,
      ),
    }
    celigo.current_channel = None
    moved_z = []

    async def move_to_well(_well, retract_z=False):
      self.assertTrue(retract_z)
      return 10, 20

    async def select(channel, **_kwargs):
      celigo.current_channel = channel

    async def move_z(position_mm):
      moved_z.append(position_mm)
      return position_mm

    async def move_both(_x, _y):
      return 0.0, 0.0

    async def no_op(*_args, **_kwargs):
      return None

    with (
      patch.multiple(
        celigo,
        camera=camera,
        move_to_well=move_to_well,
        select_channel=select,
        set_illumination_enabled=no_op,
        turn_off_illumination=no_op,
      ),
      patch.object(celigo.z_axis, "move_to", move_z),
      patch.object(celigo.galvo, "voltages_for_offset", return_value=(0.0, 0.0)),
      patch.object(celigo.galvo, "move_both", move_both),
    ):
      result = await celigo.acquire("A1", "green")
    self.assertEqual(moved_z, [2.1])
    self.assertEqual(result.z_mm, 2.1)

  async def test_acquire_failure_extinguishes_illumination(self):
    celigo = make_celigo()
    extinguished = []

    async def fail(**_kwargs):
      raise CeligoError("simulated capture failure")

    async def turn_off_illumination():
      extinguished.append(True)

    with (
      patch.multiple(
        celigo,
        _acquire_field=fail,
        turn_off_illumination=turn_off_illumination,
      ),
      self.assertRaisesRegex(CeligoError, "capture failure"),
    ):
      await celigo.acquire("A1", "brightfield")
    self.assertEqual(extinguished, [True])

  async def test_successful_acquire_extinguishes_illumination(self):
    celigo = make_celigo()
    result = object()
    extinguished = []

    async def acquire_field(**_kwargs):
      return result

    async def turn_off_illumination():
      extinguished.append(True)

    with patch.multiple(
      celigo,
      _acquire_field=acquire_field,
      turn_off_illumination=turn_off_illumination,
    ):
      self.assertIs(await celigo.acquire("A1", "brightfield"), result)
    self.assertEqual(extinguished, [True])


class TestCameraGeometry(unittest.IsolatedAsyncioTestCase):
  def test_mismatched_calibrated_geometry_is_rejected(self):
    celigo = make_celigo()
    celigo.camera.width = 2464
    celigo.camera.height = 2056
    celigo.config.calibration = make_calibration_config(
      image_width_pixels=2048,
      image_height_pixels=2048,
    )
    with self.assertRaisesRegex(CeligoError, "does not match calibrated"):
      celigo._validate_camera_geometry()

  def test_short_frame_is_rejected_before_geometry_validation(self):
    celigo = make_celigo()
    frame = CameraFrame(b"\x00\x01\x02", 2, 2, 8, 1.0, 0.0, 0.0)
    with self.assertRaisesRegex(CeligoError, "4 are required"):
      celigo._validate_frame_geometry(frame)

  async def test_direct_camera_capture_allows_full_sensor_geometry(self):
    celigo = make_celigo()
    camera = _FocusCamera()
    camera.width = 5
    camera.height = 5
    celigo.config.calibration = make_calibration_config(
      image_width_pixels=4,
      image_height_pixels=4,
    )
    with patch.object(celigo, "camera", camera):
      frame = await celigo.camera.capture(flush_frames=0)
      self.assertEqual((frame.width, frame.height), (5, 5))
      with self.assertRaisesRegex(CeligoError, "does not match calibrated"):
        await celigo.capture_frame(flush_frames=0)


class TestExternalCameraSignals(unittest.IsolatedAsyncioTestCase):
  async def test_configured_signal_inversion(self):
    celigo = make_celigo()
    celigo.config.hardware = CeligoHardwareConfig(
      external_camera_control=ExternalCameraControlConfig(
        config_version=0,
        enabled=True,
        invert_busy=True,
        invert_integration=True,
      )
    )

    async def diagnostic(operation):
      return {4: 0, 5: 1}[operation]

    with patch.object(celigo, "_send_signal_diagnostic_command", diagnostic):
      self.assertTrue(await celigo.request_is_camera_busy())
      self.assertFalse(await celigo.request_is_camera_integrating())

  async def test_unavailable_signal_returns_none(self):
    celigo = make_celigo()
    celigo.config.hardware = CeligoHardwareConfig()

    async def diagnostic(_operation):
      return 2

    with patch.object(celigo, "_send_signal_diagnostic_command", diagnostic):
      self.assertIsNone(await celigo.request_is_camera_busy())


class TestLaserSafety(unittest.IsolatedAsyncioTestCase):
  async def test_laser_is_disabled_by_default(self):
    celigo = make_celigo()
    self.assertIsInstance(celigo.laser, Laser)
    self.assertFalse(celigo.laser.enabled)
    with self.assertRaises(CeligoError):
      await celigo.laser.fire(0, 1, 0)

  async def test_constructor_enables_owned_laser(self):
    celigo = make_celigo(allow_laser=True)
    self.assertTrue(celigo.laser.enabled)

  async def test_fire_converts_delay_seconds_to_controller_ticks(self):
    celigo = make_celigo(allow_laser=True)
    transactions = []

    async def status():
      return ControllerStatus(0, 0)

    async def transact(opcode, payload=b"", retries=3):
      del retries
      transactions.append((opcode, payload))
      return b""

    async def ready(**_kwargs):
      return True

    with patch.multiple(
      celigo,
      request_controller_status=status,
      send_command=transact,
      wait_for_controller_ready=ready,
    ):
      await celigo.laser.fire(laser_index=1, shots=3, delay=0.00025)

    self.assertEqual(transactions[0][0], _CMD_FIRE_LASER)
    self.assertEqual(struct.unpack(">Hii", transactions[0][1]), (1, 3, 25))

  async def test_incomplete_fire_is_aborted(self):
    celigo = make_celigo(allow_laser=True)
    aborts = []

    async def status():
      return ControllerStatus(0, 0)

    async def transact(_opcode, _payload=b"", retries=3):
      del retries
      return b""

    async def not_ready(**_kwargs):
      return False

    async def abort():
      aborts.append(True)

    with (
      patch.multiple(
        celigo,
        request_controller_status=status,
        send_command=transact,
        wait_for_controller_ready=not_ready,
        abort_controller_operation=abort,
      ),
      self.assertRaisesRegex(TimeoutError, "did not complete"),
    ):
      await celigo.laser.fire(laser_index=0, shots=1)

    self.assertEqual(aborts, [True])

  async def test_incomplete_grid_fire_is_aborted(self):
    celigo = make_celigo(allow_laser=True)
    celigo.config.hardware = CeligoHardwareConfig(
      x_galvo=make_galvo_config(enabled=True, min_voltage=-10, max_voltage=10),
      y_galvo=make_galvo_config(enabled=True, min_voltage=-10, max_voltage=10),
    )
    aborts = []

    async def status():
      return ControllerStatus(0, 0)

    async def transact(_opcode, _payload=b"", retries=3):
      del retries
      return b""

    async def not_ready(**_kwargs):
      return False

    async def abort():
      aborts.append(True)

    with (
      patch.multiple(
        celigo,
        request_controller_status=status,
        send_command=transact,
        wait_for_controller_ready=not_ready,
        abort_controller_operation=abort,
      ),
      self.assertRaisesRegex(TimeoutError, "grid firing did not complete"),
    ):
      await celigo.laser.fire_grid(
        0,
        (0.1, 0.1),
        (1.0, 1.0),
        (0.0, 0.0),
        1,
        1,
      )

    self.assertEqual(aborts, [True])

  async def test_incomplete_targeted_fire_is_aborted(self):
    celigo = make_celigo(allow_laser=True)
    aborts = []

    async def status():
      return ControllerStatus(0, 0)

    async def targeting_status():
      return _galvo_controller_status(
        fire_table_size=32,
        points_loaded=1,
        fire_table_index=0,
      )

    async def load(_points, _center):
      return None

    async def transact(_opcode, _payload=b"", retries=3):
      del retries
      return b""

    async def not_ready(**_kwargs):
      return False

    async def abort():
      aborts.append(True)

    with (
      patch.multiple(
        celigo,
        request_controller_status=status,
        send_command=transact,
        wait_for_controller_ready=not_ready,
        abort_controller_operation=abort,
      ),
      patch.object(celigo.galvo, "request_controller_status", targeting_status),
      patch.object(celigo.laser, "_load_firing_targets", load),
      self.assertRaisesRegex(TimeoutError, "Targeted laser firing did not complete"),
    ):
      await celigo.laser.fire_targets(
        [(0.0, 0.0)],
        0,
        1,
        center_voltages=(0.0, 0.0),
      )

    self.assertEqual(aborts, [True])

  async def test_uart_command_and_response_use_component_api(self):
    celigo = make_celigo(allow_laser=True)
    transactions = []

    async def status():
      return ControllerStatus(0, 0)

    async def transact(opcode, payload=b"", retries=3):
      del retries
      transactions.append((opcode, payload))
      if opcode == _CMD_READ_LASER_COMM:
        return struct.pack(">HH", 0, 3) + b"OK\x00"
      return b""

    with patch.multiple(
      celigo,
      request_controller_status=status,
      send_command=transact,
    ):
      await celigo.laser.send_command("STATUS?")
      self.assertEqual(await celigo.laser.request_uart_response(), "OK")
    self.assertEqual(
      transactions,
      [
        (_CMD_SEND_LASER_COMM, b"STATUS?\x00"),
        (_CMD_READ_LASER_COMM, b""),
      ],
    )

  async def test_grid_payload_matches_vendor_layout(self):
    celigo = make_celigo(allow_laser=True)
    celigo.move_timeout = 1.0
    celigo.config.hardware = CeligoHardwareConfig(
      x_galvo=make_galvo_config(enabled=True, min_voltage=-10, max_voltage=10),
      y_galvo=make_galvo_config(enabled=True, min_voltage=-10, max_voltage=10),
    )
    transactions = []

    async def status():
      return ControllerStatus(0, 0)

    async def transact(opcode, payload=b"", retries=3):
      del retries
      transactions.append((opcode, payload))
      return b""

    async def ready(timeout=5.0, poll=0.01):
      del timeout, poll
      return True

    with patch.multiple(
      celigo,
      request_controller_status=status,
      send_command=transact,
      wait_for_controller_ready=ready,
    ):
      await celigo.laser.fire_grid(
        0,
        (0.1, 0.1),
        (1.0, 1.0),
        (0.0, 0.0),
        1,
        1,
        delay_between_repeats=0.0025,
      )
    self.assertEqual(transactions[0][0], _CMD_FIRE_GALVO_GRID)
    self.assertEqual(len(transactions[0][1]), 32)
    self.assertEqual(struct.unpack(">HHHHHHHiiiiH", transactions[0][1])[9], 250)

  async def test_target_table_applies_axis_inversion_to_explicit_center(self):
    celigo = make_celigo(allow_laser=True)
    celigo.config.hardware = CeligoHardwareConfig(
      x_galvo=make_galvo_config(
        enabled=True,
        invert_voltage=True,
        min_voltage=-10,
        max_voltage=10,
      ),
      y_galvo=make_galvo_config(
        enabled=True,
        invert_voltage=False,
        min_voltage=-10,
        max_voltage=10,
      ),
    )
    transactions = []

    async def status():
      return ControllerStatus(0, 0)

    async def transact(opcode, payload=b"", retries=3):
      del retries
      transactions.append((opcode, payload))
      return b""

    async def ready(**_kwargs):
      return True

    with patch.multiple(
      celigo,
      request_controller_status=status,
      send_command=transact,
      wait_for_controller_ready=ready,
    ):
      await celigo.laser._load_firing_targets([(0.1, -0.1)], (1.6, 1.5))
    self.assertEqual(transactions[0][0], _CMD_LOAD_FIRING_TABLE)
    x_dac, y_dac = struct.unpack_from(">HH", transactions[0][1], 4)
    self.assertAlmostEqual(dac_count_to_volts(x_dac), -1.7, places=3)
    self.assertAlmostEqual(dac_count_to_volts(y_dac), 1.4, places=3)

  async def test_target_fire_rechecks_interlock_after_table_load(self):
    celigo = make_celigo(allow_laser=True)
    statuses = iter((ControllerStatus(0, 0), ControllerStatus(0x0004, 0)))
    targeted = []

    async def status():
      return next(statuses)

    async def load(_points, _center):
      return None

    async def targeting_status():
      return _galvo_controller_status(
        fire_table_size=32,
        points_loaded=0,
        fire_table_index=0,
      )

    async def transact(opcode, payload=b"", retries=3):
      del payload, retries
      if opcode == _CMD_TARGETED_FIRE:
        targeted.append(opcode)
      return b""

    with (
      patch.multiple(
        celigo,
        request_controller_status=status,
        send_command=transact,
      ),
      patch.object(celigo.galvo, "request_controller_status", targeting_status),
      patch.object(celigo.laser, "_load_firing_targets", load),
      self.assertRaises(CeligoError),
    ):
      await celigo.laser.fire_targets(
        [(0.0, 0.0)],
        0,
        1,
        center_voltages=(0.0, 0.0),
      )
    self.assertEqual(targeted, [])

  async def test_empty_target_list_is_rejected_before_status_io(self):
    celigo = make_celigo(allow_laser=True)
    with self.assertRaisesRegex(ValueError, "must not be empty"):
      await celigo.laser.fire_targets([], 0, 1)

  async def test_target_fire_uses_calibrated_center_for_selected_laser(self):
    celigo = make_celigo(allow_laser=True)
    celigo.move_timeout = 1
    celigo.config.galvo_optical_calibration = GalvoOpticalCalibration(
      GalvoAxisOpticalCalibration({}, {}, laser_center_voltage=1.6, uv_laser_center_voltage=0.2),
      GalvoAxisOpticalCalibration({}, {}, laser_center_voltage=1.5, uv_laser_center_voltage=0.1),
    )
    centers = []
    targeting_statuses = iter(
      (
        _galvo_controller_status(
          fire_table_size=32,
          points_loaded=0,
          fire_table_index=0,
        ),
        _galvo_controller_status(
          fire_table_size=32,
          points_loaded=1,
          fire_table_index=1,
        ),
      )
    )

    async def status():
      return ControllerStatus(0, 0)

    async def load(_points, center):
      centers.append(center)

    async def targeting_status():
      return next(targeting_statuses)

    async def transact(_opcode, _payload=b"", retries=3):
      del retries
      return b""

    async def ready(**_kwargs):
      return True

    with (
      patch.multiple(
        celigo,
        request_controller_status=status,
        send_command=transact,
        wait_for_controller_ready=ready,
      ),
      patch.object(celigo.galvo, "request_controller_status", targeting_status),
      patch.object(celigo.laser, "_load_firing_targets", load),
    ):
      await celigo.laser.fire_targets([(0.0, 0.0)], 1, 1)
    self.assertEqual(centers, [(0.2, 0.1)])


if __name__ == "__main__":
  unittest.main()
