import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.hamilton.liquid_handlers.star.wash_station import STARWashStation


class TestSTARWashStationCommands(unittest.IsolatedAsyncioTestCase):
  """Test that STARWashStation methods produce the exact firmware commands expected."""

  async def asyncSetUp(self):
    self.mock_driver = MagicMock()
    self.mock_driver.send_command = AsyncMock()
    self.ws = STARWashStation(driver=self.mock_driver)

  # -- request_settings -------------------------------------------------------

  async def test_request_settings_station_1(self):
    self.mock_driver.send_command.return_value = {"et": 4}
    result = await self.ws.request_settings(station=1)
    self.assertEqual(result, 4)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="ET", fmt="et#", ep=1
    )

  async def test_request_settings_station_2(self):
    self.mock_driver.send_command.return_value = {"et": 0}
    result = await self.ws.request_settings(station=2)
    self.assertEqual(result, 0)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="ET", fmt="et#", ep=2
    )

  async def test_request_settings_station_3(self):
    self.mock_driver.send_command.return_value = {"et": 5}
    result = await self.ws.request_settings(station=3)
    self.assertEqual(result, 5)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="ET", fmt="et#", ep=3
    )

  async def test_request_settings_invalid_station_0(self):
    with self.assertRaises(ValueError):
      await self.ws.request_settings(station=0)

  async def test_request_settings_invalid_station_4(self):
    with self.assertRaises(ValueError):
      await self.ws.request_settings(station=4)

  # -- initialize_valves ------------------------------------------------------

  async def test_initialize_valves_station_1(self):
    await self.ws.initialize_valves(station=1)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="EJ", ep=1
    )

  async def test_initialize_valves_station_2(self):
    await self.ws.initialize_valves(station=2)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="EJ", ep=2
    )

  async def test_initialize_valves_station_3(self):
    await self.ws.initialize_valves(station=3)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="EJ", ep=3
    )

  async def test_initialize_valves_invalid_station_0(self):
    with self.assertRaises(ValueError):
      await self.ws.initialize_valves(station=0)

  async def test_initialize_valves_invalid_station_4(self):
    with self.assertRaises(ValueError):
      await self.ws.initialize_valves(station=4)

  # -- fill_chamber -----------------------------------------------------------

  async def test_fill_chamber_defaults(self):
    """Default: station=1, drain_before_refill=False, wash_fluid=1, chamber=2 -> connection 0."""
    await self.ws.fill_chamber()
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="EH",
      ep=1,
      ed=False,
      ek=0,
      eu="00",
      wait=False,
    )

  async def test_fill_chamber_wash_fluid_1_chamber_1(self):
    """wash_fluid=1, chamber=1 -> connection 1."""
    await self.ws.fill_chamber(station=1, wash_fluid=1, chamber=1)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="EH",
      ep=1,
      ed=False,
      ek=1,
      eu="00",
      wait=False,
    )

  async def test_fill_chamber_wash_fluid_2_chamber_1(self):
    """wash_fluid=2, chamber=1 -> connection 2."""
    await self.ws.fill_chamber(station=2, wash_fluid=2, chamber=1)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="EH",
      ep=2,
      ed=False,
      ek=2,
      eu="00",
      wait=False,
    )

  async def test_fill_chamber_wash_fluid_2_chamber_2(self):
    """wash_fluid=2, chamber=2 -> connection 3."""
    await self.ws.fill_chamber(station=3, wash_fluid=2, chamber=2)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="EH",
      ep=3,
      ed=False,
      ek=3,
      eu="00",
      wait=False,
    )

  async def test_fill_chamber_drain_before_refill(self):
    await self.ws.fill_chamber(station=1, drain_before_refill=True, wash_fluid=1, chamber=2)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="EH",
      ep=1,
      ed=True,
      ek=0,
      eu="00",
      wait=False,
    )

  async def test_fill_chamber_suck_time(self):
    await self.ws.fill_chamber(
      station=1,
      wash_fluid=1,
      chamber=2,
      waste_chamber_suck_time_after_sensor_change=15,
    )
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="EH",
      ep=1,
      ed=False,
      ek=0,
      eu="15",
      wait=False,
    )

  async def test_fill_chamber_invalid_station_0(self):
    with self.assertRaises(ValueError):
      await self.ws.fill_chamber(station=0)

  async def test_fill_chamber_invalid_station_4(self):
    with self.assertRaises(ValueError):
      await self.ws.fill_chamber(station=4)

  async def test_fill_chamber_invalid_wash_fluid_0(self):
    with self.assertRaises(ValueError):
      await self.ws.fill_chamber(wash_fluid=0)

  async def test_fill_chamber_invalid_wash_fluid_3(self):
    with self.assertRaises(ValueError):
      await self.ws.fill_chamber(wash_fluid=3)

  async def test_fill_chamber_invalid_chamber_0(self):
    with self.assertRaises(ValueError):
      await self.ws.fill_chamber(chamber=0)

  async def test_fill_chamber_invalid_chamber_3(self):
    with self.assertRaises(ValueError):
      await self.ws.fill_chamber(chamber=3)

  # -- drain ------------------------------------------------------------------

  async def test_drain_station_1(self):
    await self.ws.drain(station=1)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="EL", ep=1
    )

  async def test_drain_station_2(self):
    await self.ws.drain(station=2)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="EL", ep=2
    )

  async def test_drain_station_3(self):
    await self.ws.drain(station=3)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="EL", ep=3
    )

  async def test_drain_invalid_station_0(self):
    with self.assertRaises(ValueError):
      await self.ws.drain(station=0)

  async def test_drain_invalid_station_4(self):
    with self.assertRaises(ValueError):
      await self.ws.drain(station=4)
