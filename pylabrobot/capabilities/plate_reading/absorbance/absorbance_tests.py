"""Tests for AbsorbanceCapability."""

import unittest
from typing import List, Optional, Tuple

from pylabrobot.capabilities.plate_reading.absorbance.absorbance import AbsorbanceCapability
from pylabrobot.capabilities.plate_reading.absorbance.backend import AbsorbanceBackend
from pylabrobot.capabilities.plate_reading.absorbance.chatterbox import (
  AbsorbanceChatterboxBackend,
)
from pylabrobot.capabilities.plate_reading.absorbance.standard import AbsorbanceResult
from pylabrobot.device import Device
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType


def _test_plate() -> Plate:
  return Plate(
    name="test_plate",
    size_x=127.6,
    size_y=85.75,
    size_z=13.83,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.9,
      dy=7.96,
      dz=1.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.8,
      size_y=6.8,
      size_z=10.67,
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=0.17,
      max_volume=350.0,
    ),
  )


class RecordingAbsorbanceBackend(AbsorbanceBackend):
  """Backend that records all read_absorbance calls for assertion."""

  def __init__(self):
    self.calls: List[Tuple] = []

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def read_absorbance(
    self, plate: Plate, wells: List[Well], wavelength: int
  ) -> List[AbsorbanceResult]:
    self.calls.append((plate, wells, wavelength))
    data: List[List[Optional[float]]] = [
      [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
    ]
    for well in wells:
      r, c = well.get_row(), well.get_column()
      data[r][c] = 0.5
    return [AbsorbanceResult(data=data, wavelength=wavelength, temperature=None, timestamp=0.0)]


class _TestDevice(Device):
  def __init__(self, backend: AbsorbanceBackend):
    super().__init__(backend=backend)
    self._backend = backend
    self.absorbance = AbsorbanceCapability(backend=backend)
    self._capabilities = [self.absorbance]


class TestAbsorbanceCapability(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = RecordingAbsorbanceBackend()
    self.device = _TestDevice(backend=self.backend)
    await self.device.setup()
    self.plate = _test_plate()

  async def asyncTearDown(self):
    await self.device.stop()

  async def test_read_with_wells(self):
    wells = [self.plate.get_well("A1"), self.plate.get_well("B2")]
    results = await self.device.absorbance.read(
      plate=self.plate, wavelength=450, wells=wells
    )
    self.assertEqual(len(self.backend.calls), 1)
    _, recorded_wells, recorded_wl = self.backend.calls[0]
    self.assertEqual(recorded_wells, wells)
    self.assertEqual(recorded_wl, 450)
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].wavelength, 450)

  async def test_read_all_wells(self):
    results = await self.device.absorbance.read(plate=self.plate, wavelength=600)
    self.assertEqual(len(self.backend.calls), 1)
    _, recorded_wells, _ = self.backend.calls[0]
    self.assertEqual(len(recorded_wells), 96)
    self.assertEqual(results[0].wavelength, 600)

  async def test_read_requires_setup(self):
    backend = RecordingAbsorbanceBackend()
    cap = AbsorbanceCapability(backend=backend)
    with self.assertRaises(RuntimeError):
      await cap.read(plate=self.plate, wavelength=450)


class TestAbsorbanceChatterbox(unittest.IsolatedAsyncioTestCase):
  async def test_chatterbox_read(self):
    backend = AbsorbanceChatterboxBackend()
    device = _TestDevice(backend=backend)
    await device.setup()

    plate = _test_plate()
    wells = [plate.get_well("A1"), plate.get_well("H12")]
    results = await device.absorbance.read(plate=plate, wavelength=450, wells=wells)
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].wavelength, 450)
    # Only requested wells should have data
    self.assertIsNotNone(results[0].data[0][0])  # A1
    self.assertIsNotNone(results[0].data[7][11])  # H12
    self.assertIsNone(results[0].data[0][1])  # A2 not requested

    await device.stop()


if __name__ == "__main__":
  unittest.main()
