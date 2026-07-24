"""Tests for Celigo startup, camera, focus, laser safety, and persistence."""

import asyncio
import ctypes
import os
import struct
import tempfile
import threading
import unittest

from pylabrobot.celigo.camera import CameraError, CameraFrame, LumeneraCamera
from pylabrobot.celigo.celigo import (
  _CMD_FIRE_GALVO_GRID,
  _CMD_LOAD_FIRING_TABLE,
  _CMD_TARGETED_FIRE,
  _EZResponse,
  _LIMIT_OPTO_1,
  CeligoError,
  ControllerStatus,
  DeviceInfo,
  ShootingStatus,
  _dac_units_to_volts,
)
from pylabrobot.celigo.config import (
  AxisConfig,
  Calibrated2DPolynomialTransform,
  CalibrationConfig,
  CeligoHardwareConfig,
  ExternalCameraControlConfig,
  FilterWheelConfig,
  GalvoAxisOpticalCalibration,
  GalvoConfig,
  GalvoMagnificationCalibration,
  GalvoOpticalCalibration,
  IlluminationChannelConfig,
)
from pylabrobot.celigo.tests.helpers import make_celigo, stub
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb


def _filter_config() -> FilterWheelConfig:
  return FilterWheelConfig(
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
    course_position_error_window=20,
    fine_position_error_window=1,
    gain=5,
    motor_response_time=2,
    number_of_encoder_tick_per_rev=8000,
    number_of_filters=4,
  )


def _shooting_status(
  *,
  fire_table_size: int = 0,
  points_loaded: int = 0,
  fire_table_index: int = 0,
) -> ShootingStatus:
  return ShootingStatus(
    fire_table_size=fire_table_size,
    points_loaded=points_loaded,
    fire_table_index=fire_table_index,
    firing_status=0,
    galvo_capture_armed=False,
    galvo_capture_table_size=0,
  )


class TestMotorStartup(unittest.IsolatedAsyncioTestCase):
  async def test_safe_output_initialization_zeros_all_vendor_outputs(self):
    celigo = make_celigo()
    analog = []
    digital = []

    async def dac(channel, value):
      analog.append((channel, value))

    async def output(bit, on):
      digital.append((bit, on))

    stub(celigo, write_dac=dac)
    stub(celigo, set_digital_output=output)
    await celigo.initialize_safe_outputs()
    self.assertEqual(analog, [(0, 0), (1, 0), (2, 0), (3, 0)])
    self.assertEqual(digital, [(bit, False) for bit in range(12)])

  async def test_initialization_replays_vendor_tokens(self):
    celigo = make_celigo()
    celigo._initialized_motor_axes = set()
    celigo._motor_firmware_versions = {}
    commands = []

    async def send(command):
      commands.append(command)
      data = "EZStepper Controller V7.21" if command.endswith("&\r") else ""
      return _EZResponse(True, 0, data)

    stub(celigo, _send_ez=send)
    await celigo.initialize_motor(_filter_config())
    self.assertEqual(commands[0], "/4&\r")
    self.assertEqual(commands[1], "/4T\r")
    self.assertEqual(commands[2], "/4N32R\r")
    self.assertEqual(
      commands[3],
      "/4F0f1m80h30aE25600au10aC20ac1x5V30000L5000aP2R\r",
    )
    self.assertEqual(commands[4], "/4n0R\r")

  async def test_configured_move_restores_hold_current_after_failure(self):
    celigo = make_celigo()
    commands = []

    async def send(command):
      commands.append(command)
      return _EZResponse(True, 0, "")

    async def fail_wait(*_args, **_kwargs):
      raise TimeoutError("simulated timeout")

    stub(celigo, _send_ez=send)
    stub(celigo, _wait_configured_axis_ready=fail_wait)
    with self.assertRaises(TimeoutError):
      await celigo._move_configured_absolute(_filter_config(), 2000)

    self.assertEqual(commands[-1], "/4h30R\r")

  async def test_filter_move_uses_fine_window_and_retries(self):
    celigo = make_celigo()
    waits = iter((102, 100))
    wait_count = 0

    async def send(_command):
      return _EZResponse(True, 0, "")

    async def wait(*_args, **_kwargs):
      nonlocal wait_count
      wait_count += 1
      return next(waits)

    stub(celigo, _send_ez=send)
    stub(celigo, _wait_configured_axis_ready=wait)
    self.assertEqual(await celigo._move_configured_absolute(_filter_config(), 100), 100)
    self.assertEqual(wait_count, 2)

  async def test_normal_accurate_home_checks_limit_indexes_and_moves_to_minimum(self):
    celigo = make_celigo()
    axis = AxisConfig(
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
    celigo.config = CeligoHardwareConfig(x_axis=axis)
    celigo.move_timeout = 30.0
    celigo._initialized_motor_axes = {1}
    celigo._trusted_axes = set()
    celigo._motor_firmware_versions = {1: 7.21}
    encoder_positions = iter((100, 105))
    flags = iter((_LIMIT_OPTO_1, 0))
    relative_moves = []
    index_homes = []
    absolute_moves = []

    async def encoder(_axis):
      return next(encoder_positions)

    async def relative(_axis, positive, distance, velocity):
      relative_moves.append((positive, distance, velocity))
      return 0

    async def get_flags(_axis):
      return next(flags)

    async def no_op(*_args, **_kwargs):
      return None

    async def index_home(_axis, distance, velocity, mode, **_kwargs):
      index_homes.append((distance, velocity, mode))
      return 0

    async def absolute(_axis, target, **kwargs):
      absolute_moves.append((target, kwargs.get("validate_target", True)))
      return target

    stub(celigo, _get_encoder_for_config=encoder)
    stub(celigo, _send_homing_relative=relative)
    stub(celigo, request_limit_flags=get_flags)
    stub(celigo, _set_motor_mode=no_op)
    stub(celigo, _set_homing_motor_parameter=no_op)
    stub(celigo, _restore_homing_configuration=no_op)
    stub(celigo, _home_to_encoder_index=index_home)
    stub(celigo, _move_configured_absolute=absolute)

    self.assertEqual(await celigo.home("x"), -1181)
    self.assertEqual(
      relative_moves,
      [(True, 5, 3543), (False, 25000, 1181), (True, 2000, 1181)],
    )
    self.assertEqual(index_homes, [(4000, 236, 6)])
    self.assertEqual(absolute_moves, [(0, False), (-1181, True)])
    self.assertIn("x", celigo._trusted_axes)

  async def test_z_home_uses_no_index_mode_and_a_worst_case_timeout(self):
    celigo = make_celigo()
    axis = AxisConfig(
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
    celigo.config = CeligoHardwareConfig(z_axis=axis)
    celigo.move_timeout = 30.0
    celigo._initialized_motor_axes = {3}
    celigo._trusted_axes = set()
    celigo._motor_firmware_versions = {3: 7.21}
    encoders = iter((100, 105))
    flags = iter((_LIMIT_OPTO_1, 0))
    index_home = []

    async def no_op(*_args, **_kwargs):
      return None

    async def encoder(_axis):
      return next(encoders)

    async def relative(*_args, **_kwargs):
      return 0

    async def get_flags(_axis):
      return next(flags)

    async def home_index(_axis, distance, velocity, mode, **kwargs):
      index_home.append((distance, velocity, mode, kwargs["timeout"]))
      return 0

    async def absolute(_axis, target, **_kwargs):
      return target

    stub(celigo, _set_motor_mode=no_op)
    stub(celigo, _set_homing_motor_parameter=no_op)
    stub(celigo, _restore_homing_configuration=no_op)
    stub(celigo, _get_encoder_for_config=encoder)
    stub(celigo, _send_homing_relative=relative)
    stub(celigo, request_limit_flags=get_flags)
    stub(celigo, _home_to_encoder_index=home_index)
    stub(celigo, _move_configured_absolute=absolute)

    self.assertEqual(await celigo.home("z"), 126)
    self.assertEqual(index_home, [(25000, 378, 1, 68.13756613756614)])
    self.assertIn("z", celigo._trusted_axes)

  async def test_home_fails_closed_when_negative_limit_does_not_activate(self):
    celigo = make_celigo()
    axis = AxisConfig(
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
    celigo.config = CeligoHardwareConfig(x_axis=axis)
    celigo.move_timeout = 30.0
    celigo._initialized_motor_axes = {1}
    celigo._trusted_axes = {"x"}
    celigo._motor_firmware_versions = {1: 7.21}
    encoders = iter((100, 105))
    commands = []
    restored = 0

    async def encoder(_axis):
      return next(encoders)

    async def relative(*_args, **_kwargs):
      return 0

    async def get_flags(_axis):
      return 0

    async def no_op(*_args, **_kwargs):
      return None

    async def restore(_axis):
      nonlocal restored
      restored += 1

    async def send(command):
      commands.append(command)
      return _EZResponse(True, 0, "")

    stub(celigo, _get_encoder_for_config=encoder)
    stub(celigo, _send_homing_relative=relative)
    stub(celigo, request_limit_flags=get_flags)
    stub(celigo, _set_motor_mode=no_op)
    stub(celigo, _set_homing_motor_parameter=no_op)
    stub(celigo, _restore_homing_configuration=restore)
    stub(celigo, _send_ez=send)

    with self.assertRaisesRegex(CeligoError, "without activating"):
      await celigo.home("x")
    self.assertNotIn("x", celigo._trusted_axes)
    self.assertEqual(commands[-1], "/1T\r")
    self.assertEqual(restored, 1)


class TestAccurateFilterHome(unittest.IsolatedAsyncioTestCase):
  async def test_index_timeout_terminates_and_restores_configured_mode(self):
    celigo = make_celigo()
    commands = []

    async def send(command):
      commands.append(command)
      return _EZResponse(True, 0, "")

    async def timeout(*_args, **_kwargs):
      raise TimeoutError("simulated index timeout")

    stub(celigo, _send_ez=send)
    stub(celigo, _wait_configured_axis_ready=timeout)
    with self.assertRaises(TimeoutError):
      await celigo._home_to_encoder_index(_filter_config(), 2400, 600, 6, timeout=5)
    self.assertIn("/4T\r", commands)
    self.assertTrue(commands[-1].startswith("/4n"))

  async def test_scans_physical_positions_until_opto(self):
    celigo = make_celigo()
    celigo.config = CeligoHardwareConfig(dichroic_filter_wheel=_filter_config())
    celigo.move_timeout = 30.0
    celigo._motor_firmware_versions = {4: 7.21}
    celigo._discrete_home_positions = {}
    celigo._filter_home_position = None
    moves = []
    flags = iter((0, 0, _LIMIT_OPTO_1))

    async def home_index(*_args, **_kwargs):
      return 0

    async def set_mode(*_args, **_kwargs):
      return None

    async def move(axis, target, velocity):
      del axis, velocity
      moves.append(target)
      return target

    async def get_flags(_axis):
      return next(flags)

    stub(celigo, _home_to_encoder_index=home_index)
    stub(celigo, _set_motor_mode=set_mode)
    stub(celigo, _move_configured_absolute=move)
    stub(celigo, request_limit_flags=get_flags)
    self.assertEqual(await celigo.home_filter_accurate(), 4020)
    self.assertEqual(moves, [20, 2020, 4020])
    self.assertEqual(celigo._filter_home_position, 4020)


class TestCameraFrame(unittest.TestCase):
  def test_statistics_and_focus_metric(self):
    flat = CameraFrame(bytes([5] * 25), 5, 5, 8, 1.0, 0.0, 0.0)
    sharp_data = bytearray([5] * 25)
    sharp_data[12] = 250
    sharp = CameraFrame(bytes(sharp_data), 5, 5, 8, 1.0, 0.0, 0.0)
    self.assertEqual(flat.statistics(), (5, 5, 5.0))
    self.assertEqual(flat.sharpness(sample_step=1), 0.0)
    self.assertGreater(sharp.sharpness(sample_step=1), 0.0)


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

  def LucamCameraOpen(self, _index):
    return 1

  def LucamCameraClose(self, _handle):
    self.closed = True
    return 1

  def LucamStreamVideoControl(self, _handle, _operation, _unused):
    self.stream_operations.append(_operation)
    return 1

  def LucamGetFormat(self, _handle, frame_format, frame_rate):
    for name, value in self.frame_format.items():
      setattr(frame_format._obj, name, value)
    frame_rate._obj.value = 10.0
    return 1

  def LucamSetFormat(self, _handle, frame_format, _frame_rate):
    for name in self.frame_format:
      self.frame_format[name] = getattr(frame_format._obj, name)
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


class TestLumeneraCamera(unittest.IsolatedAsyncioTestCase):
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

  async def test_unsupported_pixel_format_closes_camera(self):
    library = _FakeLucamLibrary()

    def unsupported(_handle, frame_format, frame_rate):
      frame_format._obj.width = 4
      frame_format._obj.height = 3
      frame_format._obj.pixel_format = 99
      frame_rate._obj.value = 10.0
      return 1

    setattr(library, "LucamGetFormat", unsupported)
    camera = LumeneraCamera(library=library)
    with self.assertRaisesRegex(Exception, "Unsupported Lumenera pixel format"):
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

    setattr(library, "LucamCameraOpen", blocking_open)
    camera = LumeneraCamera(library=library, sdk_call_timeout=0.01)
    with self.assertRaisesRegex(CameraError, "close is queued"):
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
  async def test_center_filter_offset_inversion_and_settle_payload(self):
    celigo = make_celigo()
    celigo.config = CeligoHardwareConfig(
      x_galvo=GalvoConfig(enabled=True, min_voltage=0, max_voltage=10, invert_voltage=True),
      y_galvo=GalvoConfig(enabled=True, min_voltage=0, max_voltage=10),
    )
    celigo.magnification = 3
    celigo.reply_timeout = 2.0
    axis_x = GalvoAxisOpticalCalibration(
      magnifications={3: GalvoMagnificationCalibration(5.0, 6.5)},
      logical_filter_offsets={2: 0.2},
    )
    axis_y = GalvoAxisOpticalCalibration(
      magnifications={3: GalvoMagnificationCalibration(4.9, 6.4)},
      logical_filter_offsets={2: -0.1},
    )
    celigo.galvo_optical_calibration = GalvoOpticalCalibration(axis_x, axis_y)
    celigo.galvo_calibrations = {}
    transactions = []
    transaction_timeouts = []

    async def transact(opcode, payload=b"", retries=3):
      del retries
      transactions.append((opcode, payload))
      transaction_timeouts.append(celigo.reply_timeout)
      return b"\x00\x00"

    stub(celigo, _transact=transact)
    targets = celigo.galvo_targets_for_offset(2)
    self.assertAlmostEqual(targets[0], 5.2)
    self.assertAlmostEqual(targets[1], 4.8)
    raw = await celigo.move_galvo("x", 5.2)
    self.assertEqual(raw, -5.2)
    _index, _dac, wait, timeout = struct.unpack(">HiHH", transactions[0][1])
    self.assertEqual((wait, timeout), (1, 6000))
    self.assertEqual(transaction_timeouts, [7.0])
    self.assertEqual(celigo.reply_timeout, 2.0)
    with self.assertRaisesRegex(CeligoError, "outside configured range"):
      await celigo.move_galvo("x", 10.1)


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
  async def test_finds_best_z_and_settles_there(self):
    camera = _FocusCamera()
    celigo = make_celigo()
    stub(celigo, camera=camera)
    celigo.config = CeligoHardwareConfig(
      z_axis=AxisConfig(
        axis_index=3,
        min_position=0,
        max_position=20,
        mm_per_encoder_tick=1,
      )
    )

    async def request_encoder(_axis):
      return 10

    async def move_z(target, wait=True):
      del wait
      camera.z = target
      return target

    stub(celigo, request_encoder=request_encoder)
    stub(celigo, _move_z_ticks=move_z)
    result = await celigo.autofocus(
      center_z=10,
      span_ticks=4,
      coarse_step_ticks=2,
      fine_step_ticks=1,
      evaluator=lambda frame: frame.data[0],
      settle_seconds=0,
    )
    self.assertEqual(result.z_ticks, 12)
    self.assertEqual(camera.z, 12)

  async def test_default_acquire_applies_channel_z_offset_from_current_channel(self):
    camera = _FocusCamera()
    celigo = make_celigo()
    stub(celigo, camera=camera)
    celigo.calibration = None
    celigo.config = CeligoHardwareConfig(
      x_axis=AxisConfig(axis_index=1, min_position=0, max_position=3, mm_per_encoder_tick=1),
      y_axis=AxisConfig(axis_index=2, min_position=0, max_position=3, mm_per_encoder_tick=1),
      z_axis=AxisConfig(axis_index=3, min_position=0, max_position=3, mm_per_encoder_tick=0.01)
    )
    celigo.channels = {
      "brightfield": IlluminationChannelConfig(
        "brightfield", "Brightfield", 1, None, 0, "bf", False
      ),
      "green": IlluminationChannelConfig(
        "green", "Green", 2, 1, 0, "fl", True, z_offset_to_brightfield_mm=0.1
      ),
    }
    celigo.current_channel = "brightfield"
    moved_z = []

    async def move_to_well(_well, _plate):
      return 10, 20

    async def select(channel, **_kwargs):
      celigo.current_channel = channel

    async def encoder(_axis):
      return 100

    async def move_z(target, wait=True):
      del wait
      moved_z.append(target)
      return target

    async def move_galvos(_x, _y):
      return 0.0, 0.0

    stub(celigo, move_to_well=move_to_well)
    stub(celigo, select_channel=select)
    stub(celigo, request_encoder=encoder)
    stub(celigo, move_z=move_z)
    stub(celigo, galvo_targets_for_offset=lambda _filter, _offset: (0.0, 0.0))
    stub(celigo, move_galvos=move_galvos)
    result = await celigo.acquire("A1", "green")
    self.assertEqual(moved_z, [1.1])
    self.assertEqual(result.z_ticks, 110)

  async def test_default_acquire_uses_calibrated_z_and_channel_offset(self):
    camera = _FocusCamera()
    celigo = make_celigo()
    stub(celigo, camera=camera)
    celigo.calibration = CalibrationConfig(
      calibrated_z_position=2.0,
      image_width_pixels=5,
      image_height_pixels=5,
    )
    celigo.config = CeligoHardwareConfig(
      x_axis=AxisConfig(axis_index=1, min_position=0, max_position=3, mm_per_encoder_tick=1),
      y_axis=AxisConfig(axis_index=2, min_position=0, max_position=3, mm_per_encoder_tick=1),
      z_axis=AxisConfig(axis_index=3, min_position=0, max_position=3, mm_per_encoder_tick=0.01)
    )
    celigo.channels = {
      "green": IlluminationChannelConfig(
        "green", "Green", 2, 1, 0, "fl", True, z_offset_to_brightfield_mm=0.1
      ),
    }
    celigo.current_channel = None
    moved_z = []

    async def move_to_well(_well, _plate):
      return 10, 20

    async def select(channel, **_kwargs):
      celigo.current_channel = channel

    async def move_z(target, wait=True):
      del wait
      moved_z.append(target)
      return target

    async def move_galvos(_x, _y):
      return 0.0, 0.0

    stub(celigo, move_to_well=move_to_well)
    stub(celigo, select_channel=select)
    stub(celigo, move_z=move_z)
    stub(celigo, galvo_targets_for_offset=lambda _filter, _offset: (0.0, 0.0))
    stub(celigo, move_galvos=move_galvos)
    result = await celigo.acquire("A1", "green")
    self.assertEqual(moved_z, [2.1])
    self.assertEqual(result.z_ticks, 210)

  async def test_acquire_failure_extinguishes_illumination(self):
    celigo = make_celigo()
    extinguished = []

    async def fail(**_kwargs):
      raise CeligoError("simulated capture failure")

    async def illumination_off():
      extinguished.append(True)

    stub(celigo, _acquire=fail)
    stub(celigo, illumination_off=illumination_off)
    with self.assertRaisesRegex(CeligoError, "capture failure"):
      await celigo.acquire("A1", "brightfield")
    self.assertEqual(extinguished, [True])


class TestCameraGeometry(unittest.IsolatedAsyncioTestCase):
  def test_mismatched_calibrated_geometry_is_rejected(self):
    celigo = make_celigo()
    celigo.camera.width = 2464
    celigo.camera.height = 2056
    celigo.calibration = CalibrationConfig(image_width_pixels=2048, image_height_pixels=2048)
    with self.assertRaisesRegex(CeligoError, "does not match calibrated"):
      celigo._validate_camera_geometry()

  def test_short_frame_is_rejected_without_optional_calibration(self):
    celigo = make_celigo()
    celigo.calibration = None
    frame = CameraFrame(b"\x00\x01\x02", 2, 2, 8, 1.0, 0.0, 0.0)
    with self.assertRaisesRegex(CeligoError, "4 are required"):
      celigo._validate_frame_geometry(frame)

  async def test_raw_capture_allows_uncalibrated_full_sensor_geometry(self):
    celigo = make_celigo()
    camera = _FocusCamera()
    camera.width = 5
    camera.height = 5
    stub(celigo, camera=camera)
    celigo.calibration = CalibrationConfig(image_width_pixels=4, image_height_pixels=4)
    frame = await celigo.capture_raw_frame(flush_frames=0)
    self.assertEqual((frame.width, frame.height), (5, 5))
    with self.assertRaisesRegex(CeligoError, "does not match calibrated"):
      await celigo.capture_frame(flush_frames=0)


class TestExternalCameraSignals(unittest.IsolatedAsyncioTestCase):
  async def test_configured_signal_inversion(self):
    celigo = make_celigo()
    celigo.config = CeligoHardwareConfig(
      external_camera_control=ExternalCameraControlConfig(
        enabled=True, invert_busy=True, invert_integration=True
      )
    )

    async def diagnostic(operation):
      return {4: 0, 5: 1}[operation]

    stub(celigo, signal_diagnostics=diagnostic)
    self.assertTrue(await celigo.request_camera_busy())
    self.assertFalse(await celigo.request_camera_integration())

  async def test_malformed_signal_fails_diagnostic(self):
    celigo = make_celigo()
    celigo.config = CeligoHardwareConfig()

    async def diagnostic(_operation):
      return 2

    stub(celigo, signal_diagnostics=diagnostic)
    with self.assertRaisesRegex(CeligoError, "invalid digital value"):
      await celigo.request_camera_busy()


class TestLaserSafety(unittest.IsolatedAsyncioTestCase):
  async def test_triggered_acquisition_is_disabled_before_controller_io(self):
    celigo = make_celigo()

    async def transact(*_args, **_kwargs):
      self.fail("disabled triggered acquisition reached controller IO")

    stub(celigo, _transact=transact)
    with self.assertRaisesRegex(CeligoError, "disabled"):
      await celigo.triggered_acquisition([(0.0, 0.0)])

  async def test_laser_is_disabled_by_default(self):
    celigo = make_celigo()
    self.assertFalse(celigo.allow_laser)
    with self.assertRaises(CeligoError):
      await celigo.fire_laser(0, 1, 0)

  async def test_grid_payload_matches_vendor_layout(self):
    celigo = make_celigo()
    celigo.allow_laser = True
    celigo.move_timeout = 1.0
    celigo.config = CeligoHardwareConfig(
      x_galvo=GalvoConfig(enabled=True, min_voltage=-10, max_voltage=10),
      y_galvo=GalvoConfig(enabled=True, min_voltage=-10, max_voltage=10),
    )
    transactions = []

    async def status():
      return ControllerStatus(0)

    async def transact(opcode, payload=b"", retries=3):
      del retries
      transactions.append((opcode, payload))
      return b""

    async def ready(timeout=5.0, poll=0.01):
      del timeout, poll
      return True

    stub(celigo, request_status=status)
    stub(celigo, _transact=transact)
    stub(celigo, wait_for_ready=ready)
    await celigo.fire_laser_grid(0, (0.1, 0.1), (1.0, 1.0), (0.0, 0.0), 1, 1)
    self.assertEqual(transactions[0][0], _CMD_FIRE_GALVO_GRID)
    self.assertEqual(len(transactions[0][1]), 32)

  async def test_target_table_applies_axis_inversion_to_explicit_center(self):
    celigo = make_celigo()
    celigo.allow_laser = True
    celigo.config = CeligoHardwareConfig(
      x_galvo=GalvoConfig(enabled=True, invert_voltage=True, min_voltage=-10, max_voltage=10),
      y_galvo=GalvoConfig(enabled=True, invert_voltage=False, min_voltage=-10, max_voltage=10),
    )
    transactions = []

    async def status():
      return ControllerStatus(0)

    async def transact(opcode, payload=b"", retries=3):
      del retries
      transactions.append((opcode, payload))
      return b""

    async def ready(**_kwargs):
      return True

    stub(celigo, request_status=status)
    stub(celigo, _transact=transact)
    stub(celigo, wait_for_ready=ready)
    await celigo.load_laser_targets([(0.1, -0.1)], (1.6, 1.5))
    self.assertEqual(transactions[0][0], _CMD_LOAD_FIRING_TABLE)
    x_dac, y_dac = struct.unpack_from(">HH", transactions[0][1], 4)
    self.assertAlmostEqual(_dac_units_to_volts(x_dac), -1.7, places=3)
    self.assertAlmostEqual(_dac_units_to_volts(y_dac), 1.4, places=3)

  async def test_target_fire_rechecks_interlock_after_table_load(self):
    celigo = make_celigo()
    celigo.allow_laser = True
    statuses = iter((ControllerStatus(0), ControllerStatus(0x0004)))
    targeted = []

    async def status():
      return next(statuses)

    async def shooting_status():
      return _shooting_status(fire_table_size=32)

    async def load(_points, _center):
      return None

    async def transact(opcode, payload=b"", retries=3):
      del payload, retries
      if opcode == _CMD_TARGETED_FIRE:
        targeted.append(opcode)
      return b""

    stub(celigo, request_status=status)
    stub(celigo, request_shooting_status=shooting_status)
    stub(celigo, load_laser_targets=load)
    stub(celigo, _transact=transact)
    with self.assertRaises(CeligoError):
      await celigo.fire_laser_targets([(0.0, 0.0)], 0, 1, center_volts=(0.0, 0.0))
    self.assertEqual(targeted, [])

  async def test_empty_target_list_is_rejected_before_status_io(self):
    celigo = make_celigo()
    celigo.allow_laser = True
    with self.assertRaisesRegex(ValueError, "must not be empty"):
      await celigo.fire_laser_targets([], 0, 1)

  async def test_target_fire_uses_calibrated_center_for_selected_laser(self):
    celigo = make_celigo()
    celigo.allow_laser = True
    celigo.move_timeout = 1
    celigo.galvo_optical_calibration = GalvoOpticalCalibration(
      GalvoAxisOpticalCalibration({}, {}, laser_center_voltage=1.6, uv_laser_center_voltage=0.2),
      GalvoAxisOpticalCalibration({}, {}, laser_center_voltage=1.5, uv_laser_center_voltage=0.1),
    )
    centers = []
    shooting = iter(
      (
        _shooting_status(fire_table_size=32),
        _shooting_status(fire_table_index=1, points_loaded=1),
      )
    )

    async def status():
      return ControllerStatus(0)

    async def shooting_status():
      return next(shooting)

    async def load(_points, center):
      centers.append(center)

    async def transact(_opcode, _payload=b"", retries=3):
      del retries
      return b""

    async def ready(**_kwargs):
      return True

    stub(celigo, request_status=status)
    stub(celigo, request_shooting_status=shooting_status)
    stub(celigo, load_laser_targets=load)
    stub(celigo, _transact=transact)
    stub(celigo, wait_for_ready=ready)
    await celigo.fire_laser_targets([(0.0, 0.0)], 1, 1)
    self.assertEqual(centers, [(0.2, 0.1)])


class TestRuntimeState(unittest.TestCase):
  def test_round_trip(self):
    celigo = make_celigo()
    celigo._filter_home_position = 4020
    celigo._discrete_home_positions = {"dichroic_filter": 4020}
    celigo.current_channel = "green"
    celigo.magnification = 10
    celigo.device_info = DeviceInfo(2, (1, 3, 7), 256)
    celigo.config = CeligoHardwareConfig()
    celigo.channels = {}
    celigo.galvo_optical_calibration = None
    celigo.plate = Cor_96_wellplate_360ul_Fb(name="imaging_plate")
    celigo.galvo_calibrations = {
      2: Calibrated2DPolynomialTransform(
        forward={"LinearXTerm": (1.3, 0.0)},
        reverse={"LinearXTerm": (0.77, 0.0)},
        order=2,
        successful=True,
      )
    }
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "celigo-state.json")
      celigo.save_runtime_state(path)
      restored = make_celigo()
      restored.magnification = 10
      restored.device_info = DeviceInfo(2, (1, 3, 7), 256)
      restored.config = CeligoHardwareConfig()
      restored.channels = {}
      restored.galvo_optical_calibration = None
      restored.plate = Cor_96_wellplate_360ul_Fb(name="another_name")
      restored.load_runtime_state(path)
    self.assertIsNone(restored._filter_home_position)
    self.assertIsNone(restored.current_channel)
    self.assertEqual(restored.galvo_calibrations[2].order, 2)
    self.assertEqual(restored.galvo_calibrations[2].reverse["LinearXTerm"], (0.77, 0.0))

  def test_rejects_state_from_another_controller(self):
    celigo = make_celigo()
    celigo._filter_home_position = None
    celigo._discrete_home_positions = {}
    celigo.current_channel = None
    celigo.magnification = 3
    celigo.device_info = DeviceInfo(1, (1, 3, 0), 256)
    celigo.config = CeligoHardwareConfig()
    celigo.channels = {}
    celigo.galvo_optical_calibration = None
    celigo.galvo_calibrations = {}
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "celigo-state.json")
      celigo.save_runtime_state(path)
      celigo.device_info = DeviceInfo(2, (1, 3, 0), 256)
      with self.assertRaisesRegex(CeligoError, "different Celigo controller"):
        celigo.load_runtime_state(path)


if __name__ == "__main__":
  unittest.main()
