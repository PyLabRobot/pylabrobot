"""Tests for MicroscopyCapability."""

import unittest
from typing import List, Tuple

import pytest

pytest.importorskip("numpy")

import numpy  # noqa: E402

from pylabrobot.capabilities.microscopy.backend import MicroscopyBackend
from pylabrobot.capabilities.microscopy.chatterbox import MicroscopyChatterboxBackend
from pylabrobot.capabilities.microscopy.microscopy import MicroscopyCapability
from pylabrobot.capabilities.microscopy.standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
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


class RecordingMicroscopyBackend(MicroscopyBackend):
  """Backend that records all capture calls for assertion."""

  def __init__(self):
    self.calls: List[Tuple] = []

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
  ) -> ImagingResult:
    self.calls.append((row, column, mode, objective, exposure_time, focal_height, gain))
    return ImagingResult(
      images=[numpy.zeros((4, 4), dtype=int)],
      exposure_time=exposure_time if isinstance(exposure_time, (int, float)) else 10.0,
      focal_height=focal_height if isinstance(focal_height, (int, float)) else 0.0,
    )


class _TestMicroscope(Device):
  def __init__(self, backend: MicroscopyBackend):
    super().__init__(backend=backend)
    self._backend = backend
    self.microscopy = MicroscopyCapability(backend=backend)
    self._capabilities = [self.microscopy]


class TestMicroscopyCapability(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = RecordingMicroscopyBackend()
    self.device = _TestMicroscope(backend=self.backend)
    await self.device.setup()
    self.plate = _test_plate()

  async def asyncTearDown(self):
    await self.device.stop()

  async def test_capture_with_tuple_well(self):
    result = await self.device.microscopy.capture(
      well=(2, 5),
      mode=ImagingMode.BRIGHTFIELD,
      objective=Objective.O_10X_PL_FL,
      plate=self.plate,
      exposure_time=10.0,
      focal_height=1.5,
      gain=1.0,
    )
    self.assertEqual(len(self.backend.calls), 1)
    row, col, mode, obj, exp, fh, g = self.backend.calls[0]
    self.assertEqual(row, 2)
    self.assertEqual(col, 5)
    self.assertEqual(mode, ImagingMode.BRIGHTFIELD)
    self.assertEqual(obj, Objective.O_10X_PL_FL)
    self.assertAlmostEqual(exp, 10.0)
    self.assertAlmostEqual(fh, 1.5)
    self.assertAlmostEqual(g, 1.0)
    self.assertEqual(len(result.images), 1)

  async def test_capture_with_well_object(self):
    well = self.plate.get_well("C7")
    await self.device.microscopy.capture(
      well=well,
      mode=ImagingMode.DAPI,
      objective=Objective.O_20X_PL_FL,
      plate=self.plate,
      exposure_time=5.0,
      focal_height=2.0,
      gain=0.5,
    )
    self.assertEqual(len(self.backend.calls), 1)
    row, col, *_ = self.backend.calls[0]
    # index_of_item for C7 = 50, divmod(50, 12) = (4, 2)
    expected_idx = self.plate.index_of_item(well)
    assert expected_idx is not None
    expected_row, expected_col = divmod(expected_idx, self.plate.num_items_x)
    self.assertEqual(row, expected_row)
    self.assertEqual(col, expected_col)

  async def test_capture_machine_auto(self):
    await self.device.microscopy.capture(
      well=(0, 0),
      mode=ImagingMode.GFP,
      objective=Objective.O_4X_PL_FL,
      plate=self.plate,
    )
    self.assertEqual(len(self.backend.calls), 1)
    _, _, _, _, exp, fh, g = self.backend.calls[0]
    self.assertEqual(exp, "machine-auto")
    self.assertEqual(fh, "machine-auto")
    self.assertEqual(g, "machine-auto")

  async def test_capture_requires_setup(self):
    backend = RecordingMicroscopyBackend()
    cap = MicroscopyCapability(backend=backend)
    with self.assertRaises(RuntimeError):
      await cap.capture(
        well=(0, 0),
        mode=ImagingMode.BRIGHTFIELD,
        objective=Objective.O_4X_PL_FL,
        plate=self.plate,
      )


class TestChatterboxBackend(unittest.IsolatedAsyncioTestCase):
  async def test_chatterbox_capture(self):
    backend = MicroscopyChatterboxBackend()
    device = _TestMicroscope(backend=backend)
    await device.setup()

    plate = _test_plate()
    result = await device.microscopy.capture(
      well=(0, 0),
      mode=ImagingMode.BRIGHTFIELD,
      objective=Objective.O_4X_PL_FL,
      plate=plate,
      exposure_time=10.0,
      focal_height=1.0,
      gain=1.0,
    )
    self.assertEqual(len(result.images), 1)
    self.assertAlmostEqual(result.exposure_time, 10.0)
    self.assertAlmostEqual(result.focal_height, 1.0)

    await device.stop()


if __name__ == "__main__":
  unittest.main()
