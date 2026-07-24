"""Tests for config-driven Celigo motion, channels, filters, and drawer positions."""

import unittest
from unittest.mock import patch

from pylabrobot.celigo.celigo import Celigo, CeligoError
from pylabrobot.celigo.config import (
  AxisConfig,
  CalibrationConfig,
  CeligoHardwareConfig,
  FilterMapEntry,
  FilterWheelConfig,
  HardwareDefaultConfig,
  IlluminationChannelConfig,
  IOChannelConfig,
  IOConfig,
)
from pylabrobot.celigo.tests.helpers import FakeCamera, make_celigo, stub
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb


def _config() -> CeligoHardwareConfig:
  return CeligoHardwareConfig(
    x_axis=AxisConfig(
      axis_index=1,
      max_velocity=45,
      max_acceleration=45,
      moving_current_percentage=65,
      loading_current_percentage=55,
      min_position=0,
      max_position=20,
      mm_per_encoder_tick=1,
    ),
    y_axis=AxisConfig(
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
    dichroic_filter_wheel=FilterWheelConfig(
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
      lighting_ios=[
        IOChannelConfig(io_name="eBrightFieldIntensity", channel=0, min_voltage=0, max_voltage=10),
        IOChannelConfig(io_name="eFluorescentIntensity", channel=2, min_voltage=0, max_voltage=10),
      ],
      digital_ios=[
        IOChannelConfig(io_name="FLBit0", bit_index=4),
        IOChannelConfig(io_name="FLBit1", bit_index=5),
        IOChannelConfig(io_name="FLOnOff", bit_index=6),
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
    )
  }


class TestConfiguredFilter(unittest.IsolatedAsyncioTestCase):
  async def test_logical_filter_uses_map_and_shortest_equivalent_target(self):
    driver = make_celigo()
    driver.config = _config()
    driver._filter_home_position = 4020
    targets = []

    async def request_encoder(_axis):
      return 2020

    async def move(_axis, target, **_kwargs):
      targets.append(target)
      return target

    stub(driver, request_encoder=request_encoder)
    stub(driver, _move_configured_absolute=move)
    self.assertEqual(await driver.move_to_logical_filter(3), -1980)
    self.assertEqual(targets, [-1980])


class TestConfiguredMotorAddress(unittest.TestCase):
  def test_standard_axis_uses_loaded_address(self):
    celigo = make_celigo()
    celigo.config = CeligoHardwareConfig(x_axis=AxisConfig(axis_index=7))
    self.assertEqual(celigo._axis_index("x"), 7)


class TestConfiguredChannel(unittest.IsolatedAsyncioTestCase):
  async def test_channel_uses_configured_bits_filter_and_intensity(self):
    driver = make_celigo()
    driver.config = _config()
    driver.channels = _channels()
    driver.current_channel = None
    moves = []
    digital = []
    analog = []

    async def move_filter(logical):
      moves.append(logical)
      return 0

    async def set_digital(bit, on):
      digital.append((bit, on))

    async def write_dac(channel, value):
      analog.append((channel, value))

    stub(driver, move_to_logical_filter=move_filter)
    stub(driver, set_digital_output=set_digital)
    stub(driver, write_dac=write_dac)
    await driver.select_channel("green")

    self.assertEqual(moves, [2])
    self.assertEqual(digital, [(6, False), (4, False), (5, True), (6, True)])
    self.assertIn((2, 3276), analog)
    self.assertEqual(driver.current_channel, "green")


class TestConfiguredDrawer(unittest.IsolatedAsyncioTestCase):
  def test_load_position_comes_from_plate_and_axis_calibration(self):
    driver = make_celigo()
    driver.config = _config()
    driver.calibration = CalibrationConfig()
    driver.hardware_defaults = HardwareDefaultConfig()
    driver.plate = Cor_96_wellplate_360ul_Fb(name="imaging_plate")
    driver.load_well = "A1"
    self.assertEqual(driver._configured_load_position(None, None), (14, 8, -1))

  async def test_open_drawer_retries_and_requires_target_limit(self):
    driver = make_celigo()
    driver.config = _config()
    driver.config.z_axis = AxisConfig(
      axis_index=3, min_position=0, max_position=10, mm_per_encoder_tick=1
    )
    attempted = []

    async def no_op(*_args, **_kwargs):
      return 0

    async def no_limit(_axis):
      return 0

    async def relative(axis, steps, move_current=None):
      attempted.append((axis, steps, move_current))

    stub(driver, set_brightfield=no_op)
    stub(driver, move_z=no_op)
    stub(driver, _move_ticks=no_op)
    stub(driver, request_limit_flags=no_limit)
    stub(driver, _move_relative_to_limit=relative)
    with self.assertRaisesRegex(CeligoError, "X limit was not reached"):
      await driver.open_drawer(eject_steps=5)
    self.assertEqual(attempted, [("x", -5, 55)] * 3)


class TestCompanionConfigurationLoading(unittest.TestCase):
  def test_constructor_builds_camera_from_lucam_sdk_and_leaves_plate_unset(self):
    with (
      patch("pylabrobot.celigo.celigo.FTDI"),
      patch("pylabrobot.celigo.celigo.LumeneraCamera", FakeCamera),
    ):
      celigo = Celigo(
        lucam_sdk="/opt/lumenera/liblucamapi.so",
        hardware_defaults=HardwareDefaultConfig(),
      )

    self.assertEqual(celigo.camera.sdk_library, "/opt/lumenera/liblucamapi.so")
    self.assertIsNone(celigo.plate)

  def test_hardware_defaults_lookup_depends_only_on_missing_value(self):
    defaults = HardwareDefaultConfig()
    with (
      patch(
        "pylabrobot.celigo.celigo.CeligoHardwareConfig.locate_config_file",
        return_value="/config/HardwareDefaultConfig.xml",
      ) as locate,
      patch(
        "pylabrobot.celigo.celigo.HardwareDefaultConfig.from_xml",
        return_value=defaults,
      ),
      patch("pylabrobot.celigo.celigo.FTDI"),
    ):
      celigo = Celigo(config=CeligoHardwareConfig())

    locate.assert_called_once_with(None, "HardwareDefaultConfig.xml")
    self.assertIs(celigo.hardware_defaults, defaults)

  def test_explicit_hardware_defaults_skip_lookup(self):
    defaults = HardwareDefaultConfig()
    with (
      patch("pylabrobot.celigo.celigo.CeligoHardwareConfig.locate_config_file") as locate,
      patch("pylabrobot.celigo.celigo.FTDI"),
    ):
      celigo = Celigo(config=CeligoHardwareConfig(), hardware_defaults=defaults)

    locate.assert_not_called()
    self.assertIs(celigo.hardware_defaults, defaults)


if __name__ == "__main__":
  unittest.main()
