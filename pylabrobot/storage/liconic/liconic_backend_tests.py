# mypy: disable-error-code="attr-defined,method-assign"

import unittest
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("serial")

from pylabrobot.resources import PlateHolder
from pylabrobot.resources.carrier import PlateCarrier
from pylabrobot.storage.liconic.constants import LiconicType
from pylabrobot.storage.liconic.liconic_backend import (
  LICONIC_SITE_HEIGHT_TO_STEPS,
  ExperimentalLiconicBackend,
)
from pylabrobot.storage.liconic.racks import (
  liconic_rack_5mm_42,
  liconic_rack_17mm_22,
  liconic_rack_44mm_10,
)


class TestStepSizeFormula(unittest.TestCase):
  """Verify that the motor step sizes follow the documented formula:
  steps = round(pitch * 1713 / 50), where pitch = site_height + 6."""

  def test_step_sizes_match_formula(self):
    for site_height, claimed_steps in LICONIC_SITE_HEIGHT_TO_STEPS.items():
      if site_height == 104:
        continue  # see test_104mm_step_size_deviates_from_formula
      pitch = site_height + 6
      computed = round(pitch * 1713 / 50)
      with self.subTest(site_height=site_height, pitch=pitch):
        self.assertEqual(
          claimed_steps,
          computed,
          msg=f"site_height={site_height}, pitch={pitch}: "
          f"claimed {claimed_steps}, formula gives {computed}",
        )

  def test_104mm_step_size_deviates_from_formula(self):
    """The 104mm entry (pitch 110mm) claims 3563 steps but the formula gives 3769.
    This is either empirically determined or a data entry error.
    TODO: verify with hardware."""
    pitch = 110
    computed = round(pitch * 1713 / 50)
    self.assertEqual(computed, 3769)
    self.assertEqual(LICONIC_SITE_HEIGHT_TO_STEPS[104], 3563)
    self.assertNotEqual(LICONIC_SITE_HEIGHT_TO_STEPS[104], computed)

  def test_known_reference_points(self):
    """The two reference points from the Liconic documentation."""
    self.assertEqual(LICONIC_SITE_HEIGHT_TO_STEPS[17], 788)  # pitch 23mm
    self.assertEqual(LICONIC_SITE_HEIGHT_TO_STEPS[44], 1713)  # pitch 50mm


class TestRackConstruction(unittest.TestCase):
  def test_rack_site_count(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.assertEqual(len(rack.sites), 22)

  def test_rack_model_name(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.assertEqual(rack.model, "liconic_rack_17mm_22")

  def test_rack_site_height(self):
    rack = liconic_rack_17mm_22("test_rack")
    # All sites except the last should have size_z == site_height
    for i in range(21):
      self.assertEqual(rack.sites[i].get_size_z(), 17)

  def test_rack_default_total_height(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.assertEqual(rack.get_size_z(), 505)

  def test_rack_custom_total_height(self):
    rack = liconic_rack_5mm_42("test_rack")
    self.assertEqual(rack.get_size_z(), 505)


class TestCarrierToStepsPos(unittest.TestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")

  def test_parses_model_name(self):
    rack = liconic_rack_17mm_22("test_rack")
    self.backend._racks = [rack]
    site = rack.sites[0]
    steps, pos_num = self.backend._carrier_to_steps_pos(site)
    self.assertEqual(steps, 788)
    self.assertEqual(pos_num, 22)

  def test_5mm_rack(self):
    rack = liconic_rack_5mm_42("test_rack")
    self.backend._racks = [rack]
    site = rack.sites[0]
    steps, pos_num = self.backend._carrier_to_steps_pos(site)
    self.assertEqual(steps, 377)
    self.assertEqual(pos_num, 42)

  def test_44mm_rack(self):
    rack = liconic_rack_44mm_10("test_rack")
    self.backend._racks = [rack]
    site = rack.sites[0]
    steps, pos_num = self.backend._carrier_to_steps_pos(site)
    self.assertEqual(steps, 1713)
    self.assertEqual(pos_num, 10)

  def test_unknown_model_raises(self):
    rack = PlateCarrier(
      name="bad_rack",
      size_x=100,
      size_y=100,
      size_z=500,
      sites={},
      model="some_other_rack_17mm_22",
    )
    self.backend._racks = [rack]
    site = PlateHolder(name="s", size_x=10, size_y=10, size_z=10, pedestal_size_z=0)
    rack.assign_child_resource(site, location=None)
    with self.assertRaises(ValueError):
      self.backend._carrier_to_steps_pos(site)


class TestSiteToMN(unittest.TestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")

  def test_first_rack_first_site(self):
    rack = liconic_rack_17mm_22("rack1")
    self.backend._racks = [rack]
    m, n = self.backend._site_to_m_n(rack.sites[0])
    self.assertEqual(m, 1)  # 1-indexed rack
    self.assertEqual(n, 1)  # 1-indexed site

  def test_first_rack_last_site(self):
    rack = liconic_rack_17mm_22("rack1")
    self.backend._racks = [rack]
    m, n = self.backend._site_to_m_n(rack.sites[21])
    self.assertEqual(m, 1)
    self.assertEqual(n, 22)

  def test_second_rack(self):
    rack1 = liconic_rack_17mm_22("rack1")
    rack2 = liconic_rack_17mm_22("rack2")
    self.backend._racks = [rack1, rack2]
    m, n = self.backend._site_to_m_n(rack2.sites[0])
    self.assertEqual(m, 2)
    self.assertEqual(n, 1)


class TestValueConversions(unittest.IsolatedAsyncioTestCase):
  """Test the PLC register value conversions without actual serial IO."""

  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")
    self.backend._send_command = AsyncMock(return_value="OK")
    self.backend._wait_ready = AsyncMock()

  async def test_set_temperature_conversion(self):
    """37.5°C should become '00375' (0.1°C units)."""
    await self.backend.set_temperature(37.5)
    self.backend._send_command.assert_any_call("WR DM890 00375")

  async def test_get_temperature_conversion(self):
    """PLC value 370 should return 37.0°C."""
    self.backend._send_command = AsyncMock(return_value="370")
    result = await self.backend.get_temperature()
    self.assertAlmostEqual(result, 37.0)

  async def test_set_humidity_conversion(self):
    """0.9 fraction should become '00900' (0.1% units)."""
    await self.backend.set_humidity(0.9)
    self.backend._send_command.assert_any_call("WR DM893 00900")

  async def test_get_humidity_conversion(self):
    """PLC value 900 should return 0.9 fraction."""
    self.backend._send_command = AsyncMock(return_value="900")
    result = await self.backend.get_humidity()
    self.assertAlmostEqual(result, 0.9)

  async def test_set_co2_conversion(self):
    """0.05 fraction (5%) should become '00500' (0.01% units)."""
    await self.backend.set_co2_level(0.05)
    self.backend._send_command.assert_any_call("WR DM894 00500")

  async def test_get_co2_conversion(self):
    """PLC value 500 should return 0.05 fraction."""
    self.backend._send_command = AsyncMock(return_value="500")
    result = await self.backend.get_co2_level()
    self.assertAlmostEqual(result, 0.05)

  async def test_set_n2_conversion(self):
    """0.9 fraction (90%) should become '09000' (0.01% units)."""
    await self.backend.set_n2_level(0.9)
    self.backend._send_command.assert_any_call("WR DM895 09000")

  async def test_start_shaking_conversion(self):
    """25.0 Hz should become '00250' (0.1 Hz units)."""
    await self.backend.start_shaking(25.0)
    self.backend._send_command.assert_any_call("WR DM39 00250")

  async def test_start_shaking_fractional(self):
    """10.5 Hz should become '00105' (0.1 Hz units)."""
    await self.backend.start_shaking(10.5)
    self.backend._send_command.assert_any_call("WR DM39 00105")

  async def test_start_shaking_range_low(self):
    with self.assertRaises(ValueError):
      await self.backend.start_shaking(0.5)

  async def test_start_shaking_range_high(self):
    with self.assertRaises(ValueError):
      await self.backend.start_shaking(51.0)

  async def test_nc_model_rejects_climate(self):
    backend = ExperimentalLiconicBackend(model=LiconicType.STX44_NC, port="/dev/null")
    with self.assertRaises(NotImplementedError):
      await backend.set_temperature(37.0)
    with self.assertRaises(NotImplementedError):
      await backend.get_temperature()
    with self.assertRaises(NotImplementedError):
      await backend.set_humidity(0.5)


class TestShaking(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")
    self.backend._send_command = AsyncMock(return_value="OK")
    self.backend._wait_ready = AsyncMock()

  async def test_stop_shaking(self):
    await self.backend.stop_shaking()
    self.backend._send_command.assert_any_call("RS 1913")
    self.backend._wait_ready.assert_awaited()

  async def test_get_shaker_speed(self):
    self.backend._send_command = AsyncMock(return_value="250")
    speed = await self.backend.get_shaker_speed()
    self.assertAlmostEqual(speed, 25.0)

  async def test_shaker_status_not_implemented(self):
    with self.assertRaises(NotImplementedError):
      await self.backend.shaker_status()


class TestDoorControl(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")
    self.backend._send_command = AsyncMock(return_value="OK")
    self.backend._wait_ready = AsyncMock()

  async def test_open_door(self):
    await self.backend.open_door()
    self.backend._send_command.assert_any_call("ST 1901")
    self.backend._wait_ready.assert_awaited()

  async def test_close_door(self):
    await self.backend.close_door()
    self.backend._send_command.assert_any_call("ST 1902")
    self.backend._wait_ready.assert_awaited()


class TestSensors(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")
    self.backend._wait_ready = AsyncMock()

  async def test_check_shovel_sensor_true(self):
    self.backend._send_command = AsyncMock(side_effect=["OK", "1"])
    result = await self.backend.check_shovel_sensor()
    self.assertTrue(result)

  async def test_check_shovel_sensor_false(self):
    self.backend._send_command = AsyncMock(side_effect=["OK", "0"])
    result = await self.backend.check_shovel_sensor()
    self.assertFalse(result)

  async def test_check_shovel_sensor_unexpected(self):
    self.backend._send_command = AsyncMock(side_effect=["OK", "X"])
    with self.assertRaises(RuntimeError):
      await self.backend.check_shovel_sensor()

  async def test_check_transfer_sensor_true(self):
    self.backend._send_command = AsyncMock(return_value="1")
    result = await self.backend.check_transfer_sensor()
    self.assertTrue(result)

  async def test_check_transfer_sensor_false(self):
    self.backend._send_command = AsyncMock(return_value="0")
    result = await self.backend.check_transfer_sensor()
    self.assertFalse(result)

  async def test_check_second_transfer_sensor_true(self):
    self.backend._send_command = AsyncMock(return_value="1")
    result = await self.backend.check_second_transfer_sensor()
    self.assertTrue(result)

  async def test_check_second_transfer_sensor_false(self):
    self.backend._send_command = AsyncMock(return_value="0")
    result = await self.backend.check_second_transfer_sensor()
    self.assertFalse(result)


class TestClimateGetters(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")
    self.backend._wait_ready = AsyncMock()

  async def test_get_target_temperature(self):
    self.backend._send_command = AsyncMock(return_value="375")
    result = await self.backend.get_target_temperature()
    self.assertAlmostEqual(result, 37.5)

  async def test_get_target_humidity(self):
    self.backend._send_command = AsyncMock(return_value="900")
    result = await self.backend.get_target_humidity()
    self.assertAlmostEqual(result, 0.9)

  async def test_get_target_co2(self):
    self.backend._send_command = AsyncMock(return_value="500")
    result = await self.backend.get_target_co2_level()
    self.assertAlmostEqual(result, 0.05)

  async def test_get_n2_level(self):
    self.backend._send_command = AsyncMock(return_value="9000")
    result = await self.backend.get_n2_level()
    self.assertAlmostEqual(result, 0.9)

  async def test_get_target_n2(self):
    self.backend._send_command = AsyncMock(return_value="9000")
    result = await self.backend.get_target_n2_level()
    self.assertAlmostEqual(result, 0.9)

  async def test_nc_model_rejects_humidity(self):
    backend = ExperimentalLiconicBackend(model=LiconicType.STX44_NC, port="/dev/null")
    with self.assertRaises(NotImplementedError):
      await backend.get_humidity()
    with self.assertRaises(NotImplementedError):
      await backend.get_target_humidity()
    with self.assertRaises(NotImplementedError):
      await backend.get_target_temperature()


class TestSwapStation(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")

  async def test_turn_swap_station_home_when_swapped(self):
    self.backend._send_command = AsyncMock(return_value="1")
    await self.backend.turn_swap_station(home=True)
    self.backend._send_command.assert_any_call("RS 1912")

  async def test_turn_swap_station_swap_when_home(self):
    self.backend._send_command = AsyncMock(return_value="0")
    await self.backend.turn_swap_station(home=False)
    self.backend._send_command.assert_any_call("ST 1912")


class TestInitialize(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")
    self.backend._send_command = AsyncMock(return_value="OK")
    self.backend._wait_ready = AsyncMock()

  async def test_initialize(self):
    await self.backend.initialize()
    self.backend._send_command.assert_any_call("ST 1900")
    self.backend._send_command.assert_any_call("ST 1801")
    self.backend._wait_ready.assert_awaited()


class TestSerialization(unittest.TestCase):
  def test_serialize_roundtrip(self):
    backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/ttyUSB0")
    data = backend.serialize()
    self.assertEqual(data["port"], "/dev/ttyUSB0")
    self.assertEqual(data["model"], "STX44_IC")

    restored = ExperimentalLiconicBackend.deserialize(data)
    self.assertEqual(restored.io.port, "/dev/ttyUSB0")
    self.assertEqual(restored.model, LiconicType.STX44_IC)

  def test_deserialize_string_model(self):
    restored = ExperimentalLiconicBackend.deserialize({"port": "/dev/ttyUSB0", "model": "STX44_IC"})
    self.assertEqual(restored.model, LiconicType.STX44_IC)


class TestErrorHandling(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalLiconicBackend(model=LiconicType.STX44_IC, port="/dev/null")
    self.backend.io = AsyncMock()

  async def test_send_command_raises_on_empty_response(self):
    self.backend.io.read = AsyncMock(return_value=b"")
    with self.assertRaises(RuntimeError):
      await self.backend._send_command("RD 1915")

  async def test_send_command_raises_on_controller_error(self):
    from pylabrobot.storage.liconic.errors import LiconicControllerCommandError

    self.backend.io.read = AsyncMock(return_value=b"E1")
    with self.assertRaises(LiconicControllerCommandError):
      await self.backend._send_command("ST 1801")

  async def test_send_command_raises_on_unknown_error(self):
    self.backend.io.read = AsyncMock(return_value=b"E9")
    with self.assertRaises(RuntimeError) as ctx:
      await self.backend._send_command("ST 1801")
    self.assertIn("Unknown error", str(ctx.exception))
