# mypy: disable-error-code="attr-defined,assignment"
import unittest
from unittest.mock import AsyncMock, call, patch

import pytest

pytest.importorskip("pymodbus")

from pylabrobot.legacy.pumps import PumpArray
from pylabrobot.legacy.pumps.agrowpumps import AgrowPumpArrayBackend


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
    self.agrow_backend = AgrowPumpArrayBackend(port="simulated", address=1)

    async def _mock_setup_modbus():
      self.agrow_backend._driver._modbus = SimulatedModbusClient()

    with patch.object(self.agrow_backend._driver, "_setup_modbus", _mock_setup_modbus):
      self.pump_array = PumpArray(backend=self.agrow_backend, calibration=None)
      await self.pump_array.setup()

  async def asyncTearDown(self):
    await self.pump_array.stop()

  async def test_setup(self):
    self.assertEqual(self.agrow_backend.port, "simulated")
    self.assertEqual(self.agrow_backend.address, 1)
    self.assertEqual(
      self.agrow_backend.pump_index_to_address,
      {pump: pump + 100 for pump in range(0, 6)},
    )

  async def test_run_continuously(self):
    self.agrow_backend.modbus.write_register.reset_mock()
    await self.pump_array.run_continuously(speed=1, use_channels=[0])
    self.agrow_backend.modbus.write_register.assert_called_once_with(100, 1, unit=1)

    # invalid speed: cannot be bigger than 100
    with self.assertRaises(ValueError):
      await self.pump_array.run_continuously(speed=[101], use_channels=[0])

  async def test_run_revolutions(self):
    # not implemented for the agrow pump
    with self.assertRaises(NotImplementedError):
      await self.pump_array.run_revolutions(num_revolutions=1.0, use_channels=1)

  async def test_halt(self):
    await self.pump_array.halt()
    self.agrow_backend.modbus.write_register.assert_has_calls(
      [call(100 + i, 0, unit=1) for i in range(6)]
    )
