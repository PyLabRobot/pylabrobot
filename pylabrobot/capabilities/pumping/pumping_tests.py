import unittest
from unittest.mock import AsyncMock, Mock

from pylabrobot.capabilities.pumping.backend import PumpBackend
from pylabrobot.capabilities.pumping.calibration import PumpCalibration
from pylabrobot.capabilities.pumping.pumping import PumpingCapability


class TestPumpingCapability(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.mock_backend = Mock(spec=PumpBackend)
    self.mock_backend.run_revolutions = AsyncMock()
    self.mock_backend.run_continuously = AsyncMock()
    self.mock_backend.halt = AsyncMock()
    self.test_calibration = PumpCalibration.load_calibration(1, num_items=1)

  async def _make_cap(self, calibration=None):
    cap = PumpingCapability(backend=self.mock_backend, calibration=calibration)
    await cap._on_setup()
    return cap

  async def test_setup(self):
    cap = await self._make_cap()
    self.assertIsNone(cap.calibration)
    self.assertTrue(cap.setup_finished)

  async def test_run_revolutions(self):
    cap = await self._make_cap()
    await cap.run_revolutions(num_revolutions=1)
    self.mock_backend.run_revolutions.assert_called_once_with(num_revolutions=1)

  async def test_run_continuously(self):
    cap = await self._make_cap()
    await cap.run_continuously(speed=100)
    self.mock_backend.run_continuously.assert_called_once_with(speed=100)

  async def test_halt(self):
    cap = await self._make_cap()
    await cap.halt()
    self.mock_backend.halt.assert_called_once()

  async def test_run_for_duration(self):
    cap = await self._make_cap()
    await cap.run_for_duration(speed=1, duration=0)
    self.mock_backend.run_continuously.assert_called_with(speed=0)

  async def test_run_invalid_duration(self):
    cap = await self._make_cap()
    with self.assertRaises(ValueError):
      await cap.run_for_duration(speed=1, duration=-1)

  async def test_pump_volume_duration_mode(self):
    cap = await self._make_cap(calibration=self.test_calibration)
    cap.calibration.calibration_mode = "duration"
    cap.run_for_duration = AsyncMock()
    await cap.pump_volume(speed=1, volume=1)
    cap.run_for_duration.assert_called_once_with(speed=1, duration=1.0)

  async def test_pump_volume_revolutions_mode(self):
    cap = await self._make_cap(calibration=self.test_calibration)
    cap.calibration.calibration_mode = "revolutions"
    cap.run_revolutions = AsyncMock()
    await cap.pump_volume(speed=1, volume=1)
    cap.run_revolutions.assert_called_once_with(num_revolutions=1.0)

  async def test_pump_volume_no_calibration(self):
    cap = await self._make_cap()
    with self.assertRaises(TypeError):
      await cap.pump_volume(speed=1, volume=1)

  async def test_not_setup_raises(self):
    cap = PumpingCapability(backend=self.mock_backend)
    with self.assertRaises(RuntimeError):
      await cap.run_continuously(speed=1)


if __name__ == "__main__":
  unittest.main()
