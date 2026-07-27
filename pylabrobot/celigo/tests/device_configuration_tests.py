"""Tests for config-driven Celigo motion, channels, filters, and drawer positions."""

import inspect
import unittest
from unittest.mock import patch

from pylabrobot.celigo.celigo import Celigo, CeligoError
from pylabrobot.celigo.config import (
  CeligoHardwareConfig,
  DigitalIOConfig,
  FilterMapEntry,
  IlluminationChannelConfig,
  IOConfig,
  LightingIOConfig,
)
from pylabrobot.celigo.motion import Axis
from pylabrobot.celigo.tests.helpers import (
  FakeCamera,
  make_calibration_config,
  make_celigo,
  make_filter_wheel_config,
  make_hardware_default_config,
  make_linear_axis_config,
  make_test_config,
  stub,
)
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb


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
      number_of_encoder_tick_per_rev=8000,
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
          delay_ms=0,
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
          delay_ms=0,
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
          number_of_encoder_tick_per_rev=8000,
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
  async def test_channel_uses_configured_bits_filter_and_intensity(self):
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
    self.assertEqual(digital, [(6, False), (4, False), (5, True), (6, True)])
    self.assertIn((2, 3276), analog)
    self.assertEqual(driver.current_channel, "green")

  async def test_channel_intensity_override_is_explicitly_a_dac_count(self):
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
    await driver.select_channel("green", intensity_dac_count=1234)

    self.assertIn((2, 1234), analog)


class TestConfiguredDrawer(unittest.IsolatedAsyncioTestCase):
  def test_load_position_comes_from_plate_and_axis_calibration(self):
    driver = make_celigo(hardware=_config())
    driver.config.calibration = make_calibration_config()
    driver.config.hardware_defaults = make_hardware_default_config()
    driver.plate = Cor_96_wellplate_360ul_Fb(name="imaging_plate")
    driver.load_well = "A1"
    targets = driver._drawer_load_targets(None, None)
    self.assertAlmostEqual(targets.x_park_mm, 14.196530815027272)
    self.assertEqual(targets.y_clearance_mm, 2)
    self.assertAlmostEqual(targets.y_park_mm, 11.113591166551164)

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

    stub(driver, set_brightfield_enabled=no_op)
    stub(driver.z_axis, move_to=no_op)
    stub(driver.y_axis, move_to=no_op)
    stub(driver.x_axis, request_is_negative_limit_active=no_limit)
    stub(driver.x_axis, move_relative_to_limit=relative)
    with self.assertRaisesRegex(CeligoError, "X limit was not reached"):
      await driver.open_drawer(eject_distance_ticks=5)
    self.assertEqual(attempted, [("x", -5, 55)] * 3)


class TestCompanionConfigurationLoading(unittest.TestCase):
  def test_constructor_builds_camera_from_lucam_sdk_and_leaves_plate_unset(self):
    with (
      patch("pylabrobot.celigo.celigo.FTDI"),
      patch("pylabrobot.celigo.celigo.LumeneraCamera", FakeCamera),
    ):
      celigo = Celigo(
        lucam_sdk="/opt/lumenera/liblucamapi.so",
        config=make_test_config(),
      )

    self.assertEqual(celigo.camera.sdk_library, "/opt/lumenera/liblucamapi.so")
    self.assertIsNone(celigo.plate)

  def test_constructor_requires_an_explicit_aggregate_config(self):
    with self.assertRaisesRegex(TypeError, "config"):
      inspect.signature(Celigo).bind()

  def test_constructor_uses_explicit_aggregate_config(self):
    config = make_test_config()
    with patch("pylabrobot.celigo.celigo.FTDI"):
      celigo = Celigo(config=config)

    self.assertIs(celigo.config, config)


if __name__ == "__main__":
  unittest.main()
