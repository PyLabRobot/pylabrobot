import unittest
from unittest.mock import AsyncMock, Mock

from pylabrobot.pumps import PumpArray
from pylabrobot.pumps.calibration import PumpCalibration
from pylabrobot.pumps.errors import NotCalibratedError
from pylabrobot.pumps.pump import Pump
from pylabrobot.pumps.backend import PumpBackend, PumpArrayBackend


class TestPump(unittest.IsolatedAsyncioTestCase):
  """ Tests for the Pump class.

  Currently, only the Cole Palmer Masterflex pump is implemented.
  """

  def setUp(self):
    self.mock_backend = Mock(spec=PumpBackend)
    self.test_calibration = PumpCalibration.load_calibration(1, num_items=1)

  async def test_setup(self):
    """ Test that the Pump class can be initialized. """
    async with Pump(backend=self.mock_backend, size_x=0, size_y=0, size_z=0, name="pump") as pump:
      self.assertIsNone(pump.calibration)
      self.assertEqual(pump.backend, self.mock_backend)

  async def test_run_revolutions(self):
    """ Test that the Pump class can run for a specified number of revolutions. """
    async with Pump(backend=self.mock_backend, calibration=self.test_calibration,
                    size_x=0, size_y=0, size_z=0, name="pump") as pump:
      await pump.run_revolutions(num_revolutions=1)


class TestPumpArray(unittest.IsolatedAsyncioTestCase):
  """ Tests for the AgrowPumpArrayTester class. """

  def setUp(self):
    self.mock_backend = Mock(spec=PumpArrayBackend)
    self.mock_backend.num_channels = 6
    self.test_calibration = PumpCalibration.load_calibration(1, num_items=6)

  async def asyncSetUp(self) -> None:
    await super().asyncSetUp()
    self.pump_array = PumpArray(backend=self.mock_backend, calibration=None)
    await self.pump_array.setup()

  async def asyncTearDown(self) -> None:
    await self.pump_array.stop()
    await super().asyncTearDown()

  async def test_setup(self):
    """ Test that the AgrowPumpArrayTester class can be initialized. """
    self.assertEqual(self.pump_array.num_channels, 6)
    self.assertIsNone(self.pump_array.calibration)

  async def test_run_continuously(self):
    # valid
    await self.pump_array.run_continuously(speed=1, use_channels=[0])
    self.mock_backend.run_continuously.assert_called_once_with(speed=[1.0], use_channels=[0])

    # invalid: speed incorrect length for use_channels
    with self.assertRaises(ValueError):
      await self.pump_array.run_continuously(speed=[1, 1], use_channels=[0])

    # invalid speed: cannot be negative
    with self.assertRaises(ValueError):
      await self.pump_array.run_continuously(speed=[-1], use_channels=[0])

  async def test_invalid_channels(self):
    # invalid: max index is n-1=5
    with self.assertRaises(ValueError):
      await self.pump_array.run_continuously(speed=[1]*6, use_channels=[2, 3, 4, 5, 6, 7])

    # invalid: channels must be unique
    with self.assertRaises(ValueError):
      await self.pump_array.run_continuously(speed=[1]*6, use_channels=[1, 1, 1, 1, 1, 1])

    # invalid: too many channels
    with self.assertRaises(ValueError):
      await self.pump_array.run_continuously(speed=[1]*7, use_channels=[1, 2, 3, 4, 5, 6, 7])

  async def test_halt(self):
    await self.pump_array.halt()
    self.pump_array.backend.halt.assert_called_once() # type: ignore[attr-defined]

  async def test_run_for_duration(self):
    # can use an int or float
    await self.pump_array.run_for_duration(speed=1, use_channels=[0], duration=1)
    self.mock_backend.run_continuously.assert_called_with(speed=[0.0], use_channels=[0]) # 2nd call
    self.mock_backend.run_continuously.call_count = 2
    await self.pump_array.run_for_duration(speed=1, use_channels=[0], duration=1.0)

  async def test_run_invalid_duration(self):
    # cannot use negative int or float
    with self.assertRaises(ValueError):
      await self.pump_array.run_for_duration(speed=1, use_channels=[0], duration=-1)
    with self.assertRaises(ValueError):
      await self.pump_array.run_for_duration(speed=1, use_channels=[0], duration=-1.0)

  async def test_volume_pump_duration(self):
    self.pump_array.calibration = self.test_calibration
    self.pump_array.calibration_mode = "duration"

    # valid: can use an int or float
    self.pump_array.run_for_duration = AsyncMock() # type: ignore[method-assign]
    await self.pump_array.pump_volume(speed=1, use_channels=[0], volume=1)
    self.pump_array.run_for_duration.assert_called_once_with(speed=1, use_channels=0, duration=1.0)

  async def test_volume_pump_revolutions(self):
    self.pump_array.calibration = self.test_calibration
    self.pump_array.calibration_mode = "revolutions"

    # valid: can use an int or float
    self.pump_array.run_revolutions = AsyncMock() # type: ignore[method-assign]
    await self.pump_array.pump_volume(speed=1, use_channels=[0], volume=1)
    self.pump_array.run_revolutions.assert_called_once_with(num_revolutions=1.0, use_channels=0)

  async def test_calibration_missing(self):
    # invalid: no calibration
    with self.assertRaises(NotCalibratedError):
      await self.pump_array.pump_volume(speed=1, use_channels=[0], volume=1)

  async def test_invalid_volume(self):
    # invalid: volume cannot be negative
    self.pump_array.calibration = self.test_calibration
    with self.assertRaises(ValueError):
      await self.pump_array.pump_volume(speed=1, use_channels=[0], volume=-1)


if __name__ == "__main__":
  unittest.main()
