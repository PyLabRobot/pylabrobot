import unittest
from unittest.mock import Mock

from pylabrobot.pumps import PumpArray
from pylabrobot.pumps.calibration import PumpCalibration
from pylabrobot.pumps.pump import Pump
from pylabrobot.pumps.backend import PumpBackend, PumpArrayBackend
from pylabrobot.pumps.cole_parmer import Masterflex
from pylabrobot.pumps.agrowpumps import AgrowPumpArray

from typing import Any, Iterable

from itertools import chain, combinations


def powerset(iterable: Iterable[Any]):
  "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
  s = list(iterable)
  return chain.from_iterable(combinations(s, r) for r in range(len(s) + 1) if r > 0)


class TestPump(unittest.IsolatedAsyncioTestCase):
  """
    Tests for the Pump class.

    Currently, only the Cole Palmer Masterflex pump is implemented.
  """

  def setUp(self):
    self.mock_backend = Mock(spec=PumpBackend)
    self.masterflex_backend = Masterflex(com_port="simulated")
    self.null_calibration = PumpCalibration()
    self.test_calibration = PumpCalibration.load_calibration(1)

  async def test_setup(self):
    """
    Test that the Pump class can be initialized.
    """
    async with Pump(backend=self.mock_backend, calibration=self.null_calibration) as pump:
      self.assertEqual(pump.calibration, self.null_calibration)
      self.assertEqual(pump.backend, self.mock_backend)
      self.assertNotEqual(pump.backend, self.masterflex_backend)
    async with Pump(backend=self.masterflex_backend, calibration=self.null_calibration) as pump:
      self.assertEqual(pump.calibration, PumpCalibration.load_calibration())
      self.assertEqual(pump.backend, self.masterflex_backend)
      self.assertEqual(pump.backend.com_port, "simulated")

  async def test_run_revolutions(self):
    """
    Test that the Pump class can run for a specified number of revolutions.
    """
    async with Pump(backend=self.masterflex_backend, calibration=self.test_calibration) as pump:
      await pump.run_revolutions(num_revolutions=1)

  async def test_run_continuously(self):
    """
    Test that the Pump class can run continuously and inputs are properly handled.
    """
    async with Pump(backend=self.masterflex_backend, calibration=self.test_calibration) as pump:
      for speed in [0, 1, 100]:
        await pump.run_continuously(speed=speed)
      for speed in [-1, 101]:
        self.assertRaises(ValueError, pump.run_continuously, speed=speed)



class TestPumpArray(unittest.IsolatedAsyncioTestCase):
  """
    Tests for the AgrowPumpArray class.
  """

  def setUp(self):
    self.agrow_valid_speeds = [int(0), int(100), float(0), float(100)]
    self.agrow_invalid_speeds = [int(-1), int(101), float(-1), float(101)]
    self.agrow_use_all_channels = [1, 2, 3, 4, 5, 6]
    self.agrow_valid_volumes = [int(0), int(100), float(0), float(100)]
    self.agrow_invalid_volumes = [int(-1), float(-1)]
    self.agrow_use_all_channels_iterations = powerset(self.agrow_use_all_channels)
    self.agrow_faulty_channels = [[2, 3, 4, 5, 6, 7],
                                  [0, 1, 2, 3, 4, 5],
                                  [1, 1, 1, 1, 1, 1],
                                  [1, 2, 3, 4, 5, 6, 7]]
    self.agrow_backend = AgrowPumpArray(port="simulated", unit=1)
    self.valid_durations = [int(0), int(100), float(0), float(100)]
    self.invalid_durations = [int(-1), float(-1)]
    self.mock_backend = Mock(spec=PumpArrayBackend)
    self.null_calibration = PumpCalibration()
    self.test_calibration = PumpCalibration.load_calibration(1, num_items=6)

  async def test_setup(self):
    """
    Test that the AgrowPumpArray class can be initialized.
    """
    pump_array: PumpArray
    async with PumpArray(backend=self.agrow_backend,
                         calibration=self.null_calibration) as pump_array:
      self.assertEqual(pump_array.num_channels, 6)
      self.assertEqual(pump_array.calibration, PumpCalibration.load_calibration())
      self.assertEqual(pump_array.backend, self.agrow_backend)
      self.assertNotEqual(pump_array.backend, self.mock_backend)
      self.assertEqual(pump_array.backend.port, "simulated")
      self.assertEqual(pump_array.backend.unit, 1)

  async def test_run_continuously(self):
    """
    Test that the PumpArray class can run pumps continuously and inputs are properly handled.
    """
    async with PumpArray(backend=self.agrow_backend,
                         calibration=self.null_calibration) as pump_array:
      for use_channels in self.agrow_use_all_channels_iterations:
        for test_speed in self.agrow_valid_speeds:
          speed = [test_speed] * len(use_channels)
          incorrect_len_speed = [test_speed] * (len(use_channels) + 1)
          self.assertRaises(ValueError, pump_array.run_continuously,
                            speed=speed,
                            use_channels=use_channels)
          self.assertRaises(ValueError, pump_array.run_continuously,
                            speed=incorrect_len_speed,
                            use_channels=use_channels)
        for test_speed in self.agrow_invalid_speeds:
          speed = [test_speed] * len(use_channels)
          self.assertRaises(ValueError, pump_array.run_continuously,
                            speed=speed,
                            use_channels=use_channels)
      for use_channels in self.agrow_faulty_channels:
        for test_speed in self.agrow_valid_speeds:
          speed = [test_speed] * len(use_channels)
          self.assertRaises(ValueError, pump_array.run_continuously,
                            speed=speed,
                            use_channels=use_channels)

  async def test_run_revolutions(self):
    async with PumpArray(backend=self.agrow_backend,
                         calibration=self.null_calibration) as pump_array:
      self.assertRaises(NotImplementedError, pump_array.run_revolutions,
                        num_revolutions=1.0,
                        use_channels=1)

  async def test_halt(self):
    async with PumpArray(backend=self.agrow_backend,
                         calibration=self.null_calibration) as pump_array:
      await pump_array.halt()

  async def test_run_for_duration(self):
    async with PumpArray(backend=self.agrow_backend,
                         calibration=self.null_calibration) as pump_array:
      for use_channels in self.agrow_use_all_channels_iterations:
        for test_speed in self.agrow_valid_speeds:
          speed = [test_speed] * len(use_channels)
          incorrect_len_speed = [test_speed] * (len(use_channels) + 1)
          for duration in self.valid_durations:
            await pump_array.run_for_duration(speed=speed,
                                              use_channels=use_channels,
                                              duration=duration)
            self.assertRaises(ValueError, pump_array.run_for_duration,
                              speed=incorrect_len_speed,
                              use_channels=use_channels,
                              duration=duration)
          for duration in self.invalid_durations:
            self.assertRaises(ValueError, pump_array.run_for_duration,
                              speed=speed,
                              use_channels=use_channels,
                              duration=duration)

  async def test_volume_pump(self):
    async with PumpArray(backend=self.agrow_backend,
                        calibration=self.null_calibration) as pump_array:
      for use_channels in self.agrow_use_all_channels_iterations:
        for test_speed in self.agrow_valid_speeds:
          speed = [test_speed] * len(use_channels)
          incorrect_len_speed = [test_speed] * (len(use_channels) + 1)
          for test_volume in self.agrow_valid_volumes:
            pump_array.calibration = self.null_calibration
            volume = [test_volume] * len(use_channels)
            incorrect_len_volume = [test_volume] * (len(use_channels) + 1)
            self.assertRaises(TypeError, pump_array.pump_volume,
                              speed=speed,
                              use_channels=use_channels,
                              volume=volume)
            pump_array.calibration = self.test_calibration
            await pump_array.pump_volume(speed=test_speed,
                                         use_channels=use_channels,
                                         volume=test_volume)
            await pump_array.pump_volume(speed=speed,
                                         use_channels=use_channels,
                                         volume=volume)
            self.assertRaises(ValueError, pump_array.pump_volume,
                              speed=speed,
                              use_channels=use_channels,
                              volume=incorrect_len_volume)
            self.assertRaises(ValueError, pump_array.pump_volume,
                              speed=test_speed,
                              use_channels=use_channels,
                              volume=incorrect_len_volume)
            self.assertRaises(ValueError, pump_array.pump_volume,
                              speed=incorrect_len_speed,
                              use_channels=use_channels,
                              volume=test_volume)
            self.assertRaises(ValueError, pump_array.pump_volume,
                              speed=incorrect_len_speed,
                              use_channels=use_channels,
                              volume=volume)
          for test_volume in self.agrow_invalid_volumes:
            volume = [test_volume] * len(use_channels)
            pump_array.calibration = self.test_calibration
            self.assertRaises(ValueError, pump_array.pump_volume,
                              speed=speed,
                              use_channels=use_channels,
                              volume=volume)
            self.assertRaises(ValueError, pump_array.pump_volume,
                              speed=speed,
                              use_channels=use_channels,
                              volume=test_volume)

  async def test_stop(self):
    async with PumpArray(backend=self.agrow_backend,
                         calibration=self.null_calibration) as pump_array:
      await pump_array.stop()




if __name__ == "__main__":
  unittest.main()
