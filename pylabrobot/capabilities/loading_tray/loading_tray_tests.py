"""Tests for the LoadingTray capability."""

import unittest
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray.backend import LoadingTrayBackend
from pylabrobot.capabilities.loading_tray.loading_tray import LoadingTray
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well


class _RecordingTrayBackend(LoadingTrayBackend):
  """Records the plate passed to close()."""

  def __init__(self):
    self.closed_with: Optional[Resource] = "unset"  # type: ignore[assignment]

  async def open(self, backend_params: Optional[BackendParams] = None):
    pass

  async def close(
    self,
    backend_params: Optional[BackendParams] = None,
    plate: Optional[Resource] = None,
  ):
    self.closed_with = plate


def _plate() -> Plate:
  return Plate(
    name="plate",
    size_x=127.0,
    size_y=85.0,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=10.0,
      dy=10.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=8.0,
      size_y=8.0,
      size_z=10.0,
    ),
  )


def _tray(backend: LoadingTrayBackend, name: str) -> LoadingTray:
  return LoadingTray(backend=backend, name=name, size_x=137.0, size_y=95.0, size_z=20.0)


class TestLoadingTrayClose(unittest.IsolatedAsyncioTestCase):
  async def test_close_passes_held_plate_to_backend(self):
    backend = _RecordingTrayBackend()
    tray = _tray(backend, "tray")
    await tray._on_setup()
    plate = _plate()
    tray.assign_child_resource(plate)

    await tray.close()

    self.assertIs(backend.closed_with, plate)

  async def test_close_passes_none_when_empty(self):
    backend = _RecordingTrayBackend()
    tray = _tray(backend, "tray_empty")
    await tray._on_setup()

    await tray.close()

    self.assertIsNone(backend.closed_with)


if __name__ == "__main__":
  unittest.main()
