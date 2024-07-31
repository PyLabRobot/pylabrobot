import unittest
from unittest.mock import AsyncMock, call

from pymodbus.client import AsyncModbusSerialClient  # type: ignore

from pylabrobot.pumps import PumpArray
from pylabrobot.pumps.agrowpumps import AgrowPumpArray


class SimulatedModbusClient(AsyncModbusSerialClient):
  """
  SimulatedModbusClient allows users to simulate Modbus communication.

  Attributes:
    connected: A boolean that indicates whether the simulated client is connected.
  """

  def __init__(self, connected: bool = False):
    # pylint: disable=super-init-not-called
    self._connected = connected

  async def connect(self):
    self._connected = True

  @property
  def connected(self):
    return self._connected

  async def read_holding_registers(self, address: int, count: int, **kwargs): # type: ignore
    # pylint: disable=invalid-overridden-method
    """ Simulates reading holding registers from the AgrowPumpArray. """
    if "unit" not in kwargs:
      raise ValueError("unit must be specified")
    if address == 19:
      return_register = AsyncMock()
      return_register.registers = [16708, 13824, 0, 0, 0, 0, 0][:count]
      return return_register

  write_register = AsyncMock()

  def close(self, reconnect = False):
    assert not self.connected, "Modbus connection not established"
    self._connected = False

class TestAgrowPumps(unittest.IsolatedAsyncioTestCase):
  """ TestAgrowPumps allows users to test AgrowPumps. """

  async def asyncSetUp(self):
    self.agrow_backend = AgrowPumpArray(port="simulated", address=1)
    async def _mock_setup_modbus():
      # pylint: disable=protected-access
      self.agrow_backend._modbus = SimulatedModbusClient()

    # pylint: disable=protected-access
    self.agrow_backend._setup_modbus = _mock_setup_modbus # type: ignore[method-assign]

    self.pump_array = PumpArray(backend=self.agrow_backend, name="test_pump_array", size_x=0,
                                size_y=0, size_z=0, calibration=None)
    await self.pump_array.setup()

  async def asyncTearDown(self):
    await self.pump_array.stop()

  async def test_setup(self):
    self.assertEqual(self.agrow_backend.port, "simulated")
    self.assertEqual(self.agrow_backend.address, 1)
    self.assertEqual(self.agrow_backend._pump_index_to_address, # pylint: disable=protected-access
                      {pump: pump + 100 for pump in range(0, 6)})

  async def test_run_continuously(self):
    self.agrow_backend.modbus.write_register.reset_mock() # type: ignore[attr-defined]
    await self.pump_array.run_continuously(speed=1, use_channels=[0])
    self.agrow_backend.modbus.write_register \
      .assert_called_once_with(100, 1, unit=1) # type: ignore[attr-defined]

    # invalid speed: cannot be bigger than 100
    with self.assertRaises(ValueError):
      await self.pump_array.run_continuously(speed=[101], use_channels=[0])

  async def test_run_revolutions(self):
    # not implemented for the agrow pump
    with self.assertRaises(NotImplementedError):
      await self.pump_array.run_revolutions(num_revolutions=1.0, use_channels=1)

  async def test_halt(self):
    await self.pump_array.halt()
    self.agrow_backend.modbus.write_register.assert_has_calls( # type: ignore[attr-defined]
      [call(100 + i, 0, unit=1) for i in range(6)])
