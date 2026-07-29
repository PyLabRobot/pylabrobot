"""Tests for config-driven Celigo motion, channels, filters, and drawer positions."""

import inspect
import unittest
from unittest.mock import patch

from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb
from pylabrobot.revvity.celigo.celigo import Celigo, CeligoError
from pylabrobot.revvity.celigo.config import (
  CeligoHardwareConfig,
  DigitalIOConfig,
  FilterMapEntry,
  IlluminationChannelConfig,
  IOConfig,
  LightingIOConfig,
)
from pylabrobot.revvity.celigo.motion import Axis
from pylabrobot.revvity.celigo.tests.helpers import (
  FakeCamera,
  make_calibration_config,
  make_celigo,
  make_filter_wheel_config,
  make_hardware_default_config,
  make_linear_axis_config,
  make_test_config,
  stub,
)


def _config() -> CeligoHardwareConfig:
  return CeligoHardwareConfig(
    x_axis=make_linear_axis_config(
      axis_index=1,
      max_velocity=45,
      max_acceleration=45,
      moving_current_percentage=65,
      loading_current_percentage=55,
      min_position=0,
      max_position=20,
      mm_per_encoder_tick=1,
    ),
    y_axis=make_linear_axis_config(
      axis_index=2,
      max_velocity=40,
      max_acceleration=40,
      moving_current_percentage=65,
      loading_current_percentage=55,
      min_position=2,
      max_position=20,
      home_offset=10,
      invert_axis_direction=True,
      mm_per_encoder_tick=1,
    ),
    dichroic_filter_wheel=make_filter_wheel_config(
      axis_index=4,
      number_of_filters=4,
      encoder_ticks_per_revolution=8000,
      filter_map=[
        FilterMapEntry(logical_number=2, physical_number=1),
        FilterMapEntry(logical_number=3, physical_number=2),
        FilterMapEntry(logical_number=4, physical_number=3),
        FilterMapEntry(logical_number=1, physical_number=4),
        FilterMapEntry(logical_number=5, physical_number=4),
      ],
    ),
    io=IOConfig(
      analog_ins=[],
      lighting_ios=[
        LightingIOConfig(
          config_version=1000,
          controller_index=0,
          channel=0,
          enabled=True,
          invert=False,
          io_name="eBrightFieldIntensity",
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
          io_name="eFluorescentIntensity",
          min_voltage=0,
          max_voltage=10,
          delay=0.0,
        ),
      ],
      digital_ios=[
        DigitalIOConfig(
          config_version=1000,
          io_type="Out",
          bit_index=4,
          invert=False,
          enabled=True,
          io_name="FLBit0",
        ),
        DigitalIOConfig(
          config_version=1000,
          io_type="Out",
          bit_index=5,
          invert=False,
          enabled=True,
          io_name="FLBit1",
        ),
        DigitalIOConfig(
          config_version=1000,
          io_type="Out",
          bit_index=6,
          invert=False,
          enabled=True,
          io_name="FLOnOff",
        ),
      ],
    ),
  )


def _channels():
  return {
    "green": IlluminationChannelConfig(
      name="green",
      display_name="Green 483/536",
      logical_filter=2,
      bit_value=1,
      intensity_percent=80,
      lighting_io_name="eFluorescentIntensity",
      strobe=True,
      z_offset_to_brightfield_mm=0.0,
      mm_per_pixel_x_correction_to_brightfield=1.0,
      mm_per_pixel_y_correction_to_brightfield=1.0,
    )
  }


class TestConfiguredFilter(unittest.IsolatedAsyncioTestCase):
  async def test_logical_filter_uses_map_and_shortest_equivalent_target(self):
    driver = make_celigo(hardware=_config())
    driver.dichroic_filter._home_encoder_ticks = 4020
    targets = []

    async def request_encoder_ticks():
      return 2020

    async def move_axis(_axis, target, **_kwargs):
      targets.append(target)
      return target

    stub(driver.dichroic_filter, request_encoder_ticks=request_encoder_ticks)
    with patch.object(Axis, "move_to_ticks", new=move_axis):
      self.assertEqual(await driver.dichroic_filter.move_to(3), -1980)
    self.assertEqual(targets, [-1980])

  async def test_magnification_changer_updates_active_calibration(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        magnification_changer=make_filter_wheel_config(
          axis_index=8,
          number_of_filters=4,
          encoder_ticks_per_revolution=8000,
          filter_map=[FilterMapEntry(logical_number=5, physical_number=2)],
        )
      )
    )
    driver.magnification_changer._home_encoder_ticks = 0

    async def request_encoder_ticks():
      return 0

    async def move_axis(_axis, target, **_kwargs):
      return target

    stub(driver.magnification_changer, request_encoder_ticks=request_encoder_ticks)
    with patch.object(Axis, "move_to_ticks", new=move_axis):
      self.assertEqual(await driver.magnification_changer.move_to(5), 2000)
    self.assertEqual(driver.config.magnification, 5)


class TestConfiguredMotorAddress(unittest.TestCase):
  def test_standard_axis_uses_loaded_address(self):
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(x_axis=make_linear_axis_config(axis_index=7))
    )
    self.assertEqual(celigo.x_axis.axis_index, 7)

  def test_linear_axis_owns_position_unit_conversion(self):
    celigo = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          mm_per_encoder_tick=0.0127,
          home_offset=-18.0,
        ),
        y_axis=make_linear_axis_config(
          axis_index=2,
          mm_per_encoder_tick=0.0127,
          home_offset=71.75,
          invert_axis_direction=True,
        ),
      )
    )
    self.assertEqual(celigo.x_axis.mm_to_encoder_ticks(10.0), round((10 - 18) / 0.0127))
    for axis, position_mm in (
      (celigo.x_axis, 23.7),
      (celigo.y_axis, 12.3),
    ):
      encoder_ticks = axis.mm_to_encoder_ticks(position_mm)
      self.assertAlmostEqual(axis.encoder_ticks_to_mm(encoder_ticks), position_mm, places=1)


class TestConfiguredChannel(unittest.IsolatedAsyncioTestCase):
  async def test_channel_selection_leaves_illumination_off_until_enabled(self):
    driver = make_celigo(hardware=_config())
    driver.config.channels_by_magnification[driver.config.magnification] = _channels()
    driver.current_channel = None
    moves = []
    digital = []
    analog = []

    async def move_filter(logical):
      moves.append(logical)
      return 0

    async def set_digital(bit, on):
      digital.append((bit, on))

    async def set_analog_output_count(channel, value):
      analog.append((channel, value))

    stub(driver.dichroic_filter, move_to=move_filter)
    stub(driver, set_digital_output=set_digital)
    stub(driver, set_analog_output_count=set_analog_output_count)
    await driver.select_channel("green")

    self.assertEqual(moves, [2])
    self.assertEqual(digital, [(6, False), (4, False), (5, True)])
    self.assertEqual(analog, [(0, 0), (2, 0)])
    self.assertEqual(driver.current_channel, "green")

    await driver.set_illumination_enabled(True)
    self.assertEqual(analog[-1], (2, 3276))
    self.assertEqual(digital[-1], (6, True))

  async def test_channel_intensity_override_is_a_percentage(self):
    driver = make_celigo(hardware=_config())
    driver.config.channels_by_magnification[driver.config.magnification] = _channels()
    analog = []

    async def no_op(*_args, **_kwargs):
      return None

    async def set_analog_output_count(channel_index, dac_count):
      analog.append((channel_index, dac_count))

    stub(driver.dichroic_filter, move_to=no_op)
    stub(driver, set_digital_output=no_op)
    stub(driver, set_analog_output_count=set_analog_output_count)
    await driver.select_channel("green")
    await driver.set_illumination_enabled(True, intensity_percent=30)

    self.assertEqual(analog[-1], (2, 1228))

  async def test_channel_control_respects_inverted_outputs(self):
    hardware = _config()
    io_config = hardware.io
    assert io_config is not None
    for output in io_config.digital_ios:
      output.invert = True
    io_config.lighting_ios[1].invert = True
    driver = make_celigo(hardware=hardware)
    driver.config.channels_by_magnification[driver.config.magnification] = _channels()
    digital = []
    analog = []

    async def no_op(*_args, **_kwargs):
      return None

    async def set_digital(bit, high):
      digital.append((bit, high))

    async def set_analog(channel, value):
      analog.append((channel, value))

    stub(driver.dichroic_filter, move_to=no_op)
    stub(driver, set_digital_output=set_digital, set_analog_output_count=set_analog)
    await driver.select_channel("green")
    await driver.set_illumination_enabled(True)
    await driver.turn_off_illumination()

    self.assertEqual(digital[:3], [(6, True), (4, True), (5, False)])
    self.assertEqual(digital[3], (6, False))
    self.assertEqual(digital[4], (6, True))
    self.assertEqual(analog[:2], [(0, 0), (2, 4095)])
    self.assertEqual(analog[2], (2, 819))
    self.assertEqual(analog[-1], (2, 4095))

  async def test_channel_selection_rejects_disabled_output_before_motion(self):
    hardware = _config()
    io_config = hardware.io
    assert io_config is not None
    io_config.lighting_ios[1].enabled = False
    driver = make_celigo(hardware=hardware)
    driver.config.channels_by_magnification[driver.config.magnification] = _channels()
    moves = []

    async def move_filter(logical_filter):
      moves.append(logical_filter)
      return 0

    stub(driver.dichroic_filter, move_to=move_filter)
    with self.assertRaisesRegex(CeligoError, "is disabled"):
      await driver.select_channel("green")
    self.assertEqual(moves, [])

  async def test_channel_selection_rejects_input_only_selector_before_motion(self):
    hardware = _config()
    io_config = hardware.io
    assert io_config is not None
    io_config.digital_ios[0].io_type = "In"
    driver = make_celigo(hardware=hardware)
    driver.config.channels_by_magnification[driver.config.magnification] = _channels()
    moves = []

    async def move_filter(logical_filter):
      moves.append(logical_filter)
      return 0

    stub(driver.dichroic_filter, move_to=move_filter)
    with self.assertRaisesRegex(CeligoError, "not configured as an output"):
      await driver.select_channel("green")
    self.assertEqual(moves, [])


class TestConfiguredDrawer(unittest.IsolatedAsyncioTestCase):
  def test_sample_load_position_uses_coordinate_and_axis_calibration(self):
    driver = make_celigo(hardware=_config())
    driver.config.calibration = make_calibration_config()
    driver.config.hardware_defaults = make_hardware_default_config(
      default_plate_x_corner_stage_coordinate=2,
      default_plate_y_corner_stage_coordinate=3,
    )
    targets = driver._drawer_load_targets_from_sample_mm(4, 5)
    self.assertEqual(targets.x_park_mm, 6)
    self.assertEqual(targets.y_clearance_mm, 2)
    self.assertEqual(targets.y_park_mm, 8)

  async def test_close_to_well_requires_set_plate(self):
    driver = make_celigo(hardware=_config())
    with self.assertRaisesRegex(CeligoError, r"set_plate\(\)"):
      await driver.close_drawer("A1")

  async def test_close_to_well_delegates_to_sample_coordinates(self):
    driver = make_celigo(hardware=_config())
    driver.config.calibration = make_calibration_config()
    driver.config.hardware_defaults = make_hardware_default_config()
    driver.set_plate(Cor_96_wellplate_360ul_Fb(name="imaging_plate"))
    calls = []

    async def close_to_sample(x_mm, y_mm):
      calls.append((x_mm, y_mm))

    stub(driver, close_drawer_to_sample_mm=close_to_sample)
    await driver.close_drawer("A1")
    self.assertEqual(len(calls), 1)
    self.assertAlmostEqual(calls[0][0], 14.3)
    self.assertAlmostEqual(calls[0][1], 11.28)

  async def test_close_to_sample_retracts_z_and_moves_via_y_clearance(self):
    hardware = _config()
    hardware.z_axis = make_linear_axis_config(
      axis_index=3,
      min_position=0,
      max_position=10,
      mm_per_encoder_tick=1,
    )
    driver = make_celigo(hardware=hardware)
    driver.config.calibration = make_calibration_config()
    driver.config.hardware_defaults = make_hardware_default_config(
      default_plate_x_corner_stage_coordinate=2,
      default_plate_y_corner_stage_coordinate=3,
    )
    driver.current_channel = "brightfield"
    calls = []

    async def turn_off():
      calls.append(("illumination", None))

    async def move_z(position):
      calls.append(("z", position))
      return position

    async def move_x(position):
      calls.append(("x", position))
      return position

    async def move_y(position):
      calls.append(("y", position))
      return position

    stub(driver, turn_off_illumination=turn_off)
    stub(driver.z_axis, move_to=move_z)
    stub(driver.x_axis, move_to=move_x)
    stub(driver.y_axis, move_to=move_y)
    await driver.close_drawer_to_sample_mm(4, 5)
    self.assertIsNone(driver.current_channel)
    self.assertEqual(
      calls,
      [
        ("illumination", None),
        ("z", 0),
        ("y", 2),
        ("x", 6),
        ("y", 8),
      ],
    )

  async def test_close_to_sample_rejects_invalid_targets_before_motion(self):
    driver = make_celigo(hardware=_config())
    driver.config.calibration = make_calibration_config()
    driver.config.hardware_defaults = make_hardware_default_config()

    async def unexpected():
      self.fail("invalid drawer target changed illumination")

    stub(driver, turn_off_illumination=unexpected)
    with self.assertRaisesRegex(ValueError, "must be finite"):
      await driver.close_drawer_to_sample_mm(float("nan"), 5)
    with self.assertRaisesRegex(CeligoError, "outside configured range"):
      await driver.close_drawer_to_sample_mm(100, 5)

  async def test_open_drawer_retries_and_requires_target_limit(self):
    hardware = _config()
    hardware.z_axis = make_linear_axis_config(
      axis_index=3, min_position=0, max_position=10, mm_per_encoder_tick=1
    )
    driver = make_celigo(hardware=hardware)
    attempted = []

    async def no_op(*_args, **_kwargs):
      return 0

    async def no_limit():
      return False

    async def relative(
      distance_ticks,
      move_current_percent=None,
    ):
      attempted.append(("x", distance_ticks, move_current_percent))

    stub(driver, turn_off_illumination=no_op)
    stub(driver.z_axis, move_to=no_op)
    stub(driver.y_axis, move_to=no_op)
    stub(driver.x_axis, request_is_negative_limit_active=no_limit)
    stub(
      driver.x_axis,
      _limit_move_distance_ticks=lambda: 5,
      _move_relative_to_limit=relative,
    )
    stub(driver.y_axis, _limit_move_distance_ticks=lambda: 5)
    with self.assertRaisesRegex(CeligoError, "X limit was not reached"):
      await driver.open_drawer()
    self.assertEqual(attempted, [("x", -5, 55)] * 3)


class TestCompanionConfigurationLoading(unittest.TestCase):
  def test_constructor_builds_camera_from_lucam_sdk(self):
    with (
      patch("pylabrobot.revvity.celigo.celigo.FTDI"),
      patch("pylabrobot.revvity.celigo.celigo.LumeneraCamera", FakeCamera),
    ):
      celigo = Celigo(
        lucam_sdk="/opt/lumenera/liblucamapi.so",
        config=make_test_config(),
      )

    self.assertIsInstance(celigo.camera, FakeCamera)
    camera = celigo.camera
    assert isinstance(camera, FakeCamera)
    self.assertEqual(camera.sdk_library, "/opt/lumenera/liblucamapi.so")

  def test_constructor_requires_an_explicit_aggregate_config(self):
    with self.assertRaisesRegex(TypeError, "config"):
      inspect.signature(Celigo).bind()

  def test_constructor_uses_explicit_aggregate_config(self):
    config = make_test_config()
    with patch("pylabrobot.revvity.celigo.celigo.FTDI"):
      celigo = Celigo(config=config)

    self.assertIs(celigo.config, config)


if __name__ == "__main__":
  unittest.main()
