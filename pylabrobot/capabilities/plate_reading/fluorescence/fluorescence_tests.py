"""Tests for FluorescenceCapability."""

import unittest
from typing import List

from pylabrobot.capabilities.plate_reading.fluorescence.backend import FluorescenceBackend
from pylabrobot.capabilities.plate_reading.fluorescence.chatterbox import (
  FluorescenceChatterboxBackend,
)
from pylabrobot.capabilities.plate_reading.fluorescence.fluorescence import FluorescenceCapability
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
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


class RecordingFluorescenceBackend(FluorescenceBackend):
  """Backend that records all calls for assertion."""

  def __init__(self):
    self.calls: List[tuple] = []

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[FluorescenceResult]:
    self.calls.append(
      ("read_fluorescence", len(wells), excitation_wavelength, emission_wavelength, focal_height)
    )
    data = [[0.0] * plate.num_items_x for _ in range(plate.num_items_y)]
    return [
      FluorescenceResult(
        data=data,
        excitation_wavelength=excitation_wavelength,
        emission_wavelength=emission_wavelength,
        temperature=25.0,
        timestamp=0.0,
      )
    ]


class _TestDevice(Device):
  def __init__(self, backend: FluorescenceBackend):
    super().__init__(backend=backend)
    self.fluorescence = FluorescenceCapability(backend=backend)
    self._capabilities = [self.fluorescence]


class TestFluorescenceCapability(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = RecordingFluorescenceBackend()
    self.device = _TestDevice(backend=self.backend)
    await self.device.setup()
    self.plate = _test_plate()

  async def asyncTearDown(self):
    await self.device.stop()

  async def test_read_with_wells(self):
    wells = [self.plate.get_well("A1"), self.plate.get_well("B2")]
    results = await self.device.fluorescence.read(
      plate=self.plate,
      excitation_wavelength=485,
      emission_wavelength=528,
      focal_height=8.5,
      wells=wells,
    )
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].excitation_wavelength, 485)
    self.assertEqual(results[0].emission_wavelength, 528)
    self.assertEqual(len(self.backend.calls), 1)
    _, n_wells, ex_wl, em_wl, fh = self.backend.calls[0]
    self.assertEqual(n_wells, 2)
    self.assertEqual(ex_wl, 485)
    self.assertEqual(em_wl, 528)
    self.assertAlmostEqual(fh, 8.5)

  async def test_read_all_wells(self):
    results = await self.device.fluorescence.read(
      plate=self.plate,
      excitation_wavelength=485,
      emission_wavelength=528,
      focal_height=8.5,
    )
    self.assertEqual(len(results), 1)
    _, n_wells, *_ = self.backend.calls[0]
    self.assertEqual(n_wells, 96)

  async def test_read_requires_setup(self):
    backend = RecordingFluorescenceBackend()
    cap = FluorescenceCapability(backend=backend)
    with self.assertRaises(RuntimeError):
      await cap.read(
        plate=self.plate,
        excitation_wavelength=485,
        emission_wavelength=528,
        focal_height=8.5,
      )


class TestFluorescenceChatterbox(unittest.IsolatedAsyncioTestCase):
  async def test_chatterbox_read(self):
    backend = FluorescenceChatterboxBackend()
    device = _TestDevice(backend=backend)
    await device.setup()
    plate = _test_plate()

    wells = [plate.get_well("A1"), plate.get_well("C3")]
    results = await device.fluorescence.read(
      plate=plate,
      excitation_wavelength=485,
      emission_wavelength=528,
      focal_height=8.5,
      wells=wells,
    )
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].excitation_wavelength, 485)
    self.assertEqual(results[0].emission_wavelength, 528)
    self.assertEqual(results[0].data[0][0], 0.0)
    self.assertIsNone(results[0].data[1][0])

    await device.stop()


if __name__ == "__main__":
  unittest.main()
