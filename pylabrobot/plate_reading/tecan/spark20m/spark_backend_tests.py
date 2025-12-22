import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.plate_reading.tecan.spark20m.spark_backend import SparkBackend, SparkDevice
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

sys.modules["usb.core"] = MagicMock()
sys.modules["usb.util"] = MagicMock()


class TestSparkBackend(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    # Patch SparkReaderAsync
    self.reader_patcher = patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_backend.SparkReaderAsync"
    )
    self.MockReaderClass = self.reader_patcher.start()
    self.mock_reader = self.MockReaderClass.return_value

    # Mock reading context manager
    self.mock_reading_cm = MagicMock()
    self.mock_reading_cm.__aenter__ = AsyncMock()
    self.mock_reading_cm.__aexit__ = AsyncMock()
    self.mock_reader.reading.return_value = self.mock_reading_cm

    # Patch Processors
    self.abs_proc_patcher = patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_backend.AbsorbanceProcessor"
    )
    self.MockAbsProcClass = self.abs_proc_patcher.start()
    self.mock_abs_proc = self.MockAbsProcClass.return_value

    self.fluo_proc_patcher = patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_backend.FluorescenceProcessor"
    )
    self.MockFluoProcClass = self.fluo_proc_patcher.start()
    self.mock_fluo_proc = self.MockFluoProcClass.return_value

    self.backend = SparkBackend()

  async def asyncTearDown(self):
    self.reader_patcher.stop()
    self.abs_proc_patcher.stop()
    self.fluo_proc_patcher.stop()

  async def test_setup(self):
    await self.backend.setup()
    self.mock_reader.connect.assert_called_once()
    # Verify that init_module was called and it used the reading context
    self.mock_reader.reading.assert_called_with(SparkDevice.PLATE_TRANSPORT)

  async def test_open(self):
    await self.backend.open()
    self.mock_reader.reading.assert_called_with(SparkDevice.PLATE_TRANSPORT)

  async def test_read_absorbance(self):
    # Mock background read
    stop_event = MagicMock()
    bg_task: asyncio.Future = asyncio.Future()
    bg_task.set_result(None)
    self.mock_reader.start_background_read = AsyncMock(return_value=(bg_task, stop_event, []))

    self.mock_abs_proc.process.return_value = [[0.5]]

    plate = MagicMock(spec=Plate)
    plate.num_items_x = 2
    plate.num_items_y = 2
    plate.get_size_y.return_value = 100

    well = MagicMock(spec=Well)
    well.parent = plate
    well.get_row.return_value = 0
    well.get_column.return_value = 0
    well.location = MagicMock()
    well.location.x = 0
    well.location.y = 0
    well.location.z = 0
    well.get_anchor.return_value = MagicMock()

    plate.get_item.return_value = well

    results = await self.backend.read_absorbance(plate, None, wavelength=600)

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["wavelength"], 600)
    self.assertEqual(results[0]["data"], [[0.5]])

  async def test_read_fluorescence(self):
    # Mock background read
    stop_event = MagicMock()
    bg_task: asyncio.Future = asyncio.Future()
    bg_task.set_result(None)
    self.mock_reader.start_background_read = AsyncMock(return_value=(bg_task, stop_event, []))

    self.mock_fluo_proc.process.return_value = [[100.0]]

    plate = MagicMock(spec=Plate)
    plate.num_items_x = 2
    plate.num_items_y = 2
    plate.get_size_y.return_value = 100

    well = MagicMock(spec=Well)
    well.parent = plate
    well.get_row.return_value = 0
    well.get_column.return_value = 0
    well.location = MagicMock()
    well.location.x = 0
    well.location.y = 0
    well.location.z = 0
    well.get_anchor.return_value = MagicMock()

    plate.get_item.return_value = well

    results = await self.backend.read_fluorescence(
      plate, [well], excitation_wavelength=480, emission_wavelength=520
    )

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["ex_wavelength"], 480)
    self.assertEqual(results[0]["em_wavelength"], 520)
    self.assertEqual(results[0]["data"], [[100.0]])

  async def test_get_average_temperature(self):
    # Mock reader messages
    self.mock_reader.msgs = [
      {"number": 100, "args": ["2500"]},  # 25.00
      {"number": 100, "args": ["2600"]},  # 26.00
      {"number": 200, "args": ["something else"]},
    ]

    temp = await self.backend.get_average_temperature()
    self.assertEqual(temp, 25.5)

  async def test_get_average_temperature_empty(self):
    self.mock_reader.msgs = []
    temp = await self.backend.get_average_temperature()
    self.assertIsNone(temp)


if __name__ == "__main__":
  unittest.main()
