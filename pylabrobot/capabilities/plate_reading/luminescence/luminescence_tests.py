"""Tests for Luminescence."""

import unittest
from typing import List, Optional

from pylabrobot.capabilities.plate_reading.luminescence.backend import LuminescenceBackend
from pylabrobot.capabilities.plate_reading.luminescence.chatterbox import (
  LuminescenceChatterboxBackend,
)
from pylabrobot.capabilities.plate_reading.luminescence.luminescence import Luminescence
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.serializer import SerializableMixin


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


class RecordingLuminescenceBackend(LuminescenceBackend):
  """Backend that records all calls for assertion."""

  def __init__(self):
    self.calls: List[tuple] = []

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    self.calls.append(("read_luminescence", len(wells), focal_height))
    data: List[List[Optional[float]]] = [
      [0.0] * plate.num_items_x for _ in range(plate.num_items_y)
    ]
    return [LuminescenceResult(data=data, temperature=25.0, timestamp=0.0)]


class TestLuminescence(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = RecordingLuminescenceBackend()
    self.cap = Luminescence(backend=self.backend)
    await self.cap._on_setup()
    self.plate = _test_plate()

  async def test_read_with_wells(self):
    wells = [self.plate.get_well("A1"), self.plate.get_well("B2")]
    results = await self.cap.read(plate=self.plate, focal_height=13.0, wells=wells)
    self.assertEqual(len(results), 1)
    self.assertEqual(len(self.backend.calls), 1)
    _, n_wells, fh = self.backend.calls[0]
    self.assertEqual(n_wells, 2)
    self.assertAlmostEqual(fh, 13.0)

  async def test_read_all_wells(self):
    results = await self.cap.read(plate=self.plate, focal_height=13.0)
    self.assertEqual(len(results), 1)
    _, n_wells, _ = self.backend.calls[0]
    self.assertEqual(n_wells, 96)

  async def test_read_requires_setup(self):
    backend = RecordingLuminescenceBackend()
    cap = Luminescence(backend=backend)
    with self.assertRaises(RuntimeError):
      await cap.read(plate=self.plate, focal_height=13.0)


class TestLuminescenceChatterbox(unittest.IsolatedAsyncioTestCase):
  async def test_chatterbox_read(self):
    backend = LuminescenceChatterboxBackend()
    cap = Luminescence(backend=backend)
    await cap._on_setup()
    plate = _test_plate()

    wells = [plate.get_well("A1"), plate.get_well("C3")]
    results = await cap.read(plate=plate, focal_height=13.0, wells=wells)
    self.assertEqual(len(results), 1)
    # A1 = row 0, col 0 => measured
    self.assertEqual(results[0].data[0][0], 0.0)
    # B1 = row 1, col 0 => not measured
    self.assertIsNone(results[0].data[1][0])


if __name__ == "__main__":
  unittest.main()
