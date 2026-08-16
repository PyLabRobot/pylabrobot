# mypy: disable-error-code="attr-defined,assignment"
import unittest
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pymodbus")

from pylabrobot.agrowpumps import AgrowPumpArray


class SimulatedModbusClient:
  """Duck-typed modbus client for testing."""

  def __init__(self):
    self._connected = False
    self.write_register = AsyncMock()

  async def connect(self):
    self._connected = True

  @property
  def connected(self):
    return self._connected

  async def read_holding_registers(self, address: int, count: int, **kwargs):
    if "unit" not in kwargs:
      raise ValueError("unit must be specified")
    if address == 19:
      result = AsyncMock()
      result.registers = [16708, 13824, 0, 0, 0, 0, 0][:count]
      return result

  def close(self, reconnect=False):
    self._connected = False


class TestAgrowPumps(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.device = AgrowPumpArray(port="simulated", address=1)

    async def _mock_setup_modbus():
      self.device._modbus = SimulatedModbusClient()

    with patch.object(self.device, "_setup_modbus", _mock_setup_modbus):
      await self.device.setup()

  async def asyncTearDown(self):
    await self.device.stop()

  async def test_setup(self):
    self.assertEqual(self.device.port, "simulated")
    self.assertEqual(self.device.address, 1)
    self.assertEqual(len(self.device.channels), 6)
    self.assertEqual(
      self.device._pump_index_to_address,
      {pump: pump + 100 for pump in range(0, 6)},
    )

  async def test_run_continuously(self):
    self.device.modbus.write_register.reset_mock()
    await self.device.channels[0].run_continuously(speed=1)
    self.device.modbus.write_register.assert_called_once_with(100, 1, unit=1)

    # invalid speed: cannot be bigger than 100
    with self.assertRaises(ValueError):
      await self.device.channels[0].run_continuously(speed=101)

  async def test_run_revolutions(self):
    with self.assertRaises(NotImplementedError):
      await self.device.channels[0].run_revolutions(num_revolutions=1.0)

  async def test_halt_single_channel(self):
    self.device.modbus.write_register.reset_mock()
    await self.device.channels[2].halt()
    self.device.modbus.write_register.assert_called_once_with(102, 0, unit=1)
