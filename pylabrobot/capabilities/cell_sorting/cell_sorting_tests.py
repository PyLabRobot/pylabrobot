"""Tests for the CellSorter capability."""

import unittest
from typing import List, Optional, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.cell_sorting.backend import CellSorterBackend
from pylabrobot.capabilities.cell_sorting.cell_sorting import CellSorter
from pylabrobot.capabilities.cell_sorting.chatterbox import CellSorterChatterboxBackend


class _RecordingBackend(CellSorterBackend):
  """Records the sequence of primitive calls the frontend makes."""

  def __init__(self) -> None:
    self.calls: List[Tuple[str, tuple]] = []

  async def get_status(self) -> str:
    self.calls.append(("get_status", ()))
    return "idle"

  async def load_template(self, name: str) -> None:
    self.calls.append(("load_template", (name,)))

  async def set_deposition(self, cells_per_well: int, plate_format: str) -> None:
    self.calls.append(("set_deposition", (cells_per_well, plate_format)))

  async def prime(self) -> None:
    self.calls.append(("prime", ()))

  async def start_sort(
    self,
    wells: int,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    self.calls.append(("start_sort", (wells,)))

  async def wait_for_completion(self, poll_interval: float, timeout: float) -> None:
    self.calls.append(("wait_for_completion", ()))

  async def abort(self) -> None:
    self.calls.append(("abort", ()))

  async def clean(self) -> None:
    self.calls.append(("clean", ()))


class TestCellSorter(unittest.IsolatedAsyncioTestCase):
  async def test_sort_to_plate_sequences_primitives_in_order(self):
    backend = _RecordingBackend()
    sorter = CellSorter(backend=backend)
    await sorter._on_setup()

    await sorter.sort_to_plate(cells_per_well=25, wells=96, template="singlet_deposit")

    self.assertEqual(
      [name for name, _ in backend.calls],
      ["load_template", "set_deposition", "prime", "start_sort", "wait_for_completion", "clean"],
    )
    self.assertEqual(backend.calls[0], ("load_template", ("singlet_deposit",)))
    self.assertEqual(backend.calls[1], ("set_deposition", (25, "96")))
    self.assertEqual(backend.calls[3], ("start_sort", (96,)))

  async def test_requires_setup_before_use(self):
    sorter = CellSorter(backend=_RecordingBackend())
    with self.assertRaises(RuntimeError):
      await sorter.get_status()

  async def test_rejects_invalid_arguments(self):
    sorter = CellSorter(backend=_RecordingBackend())
    await sorter._on_setup()
    with self.assertRaises(ValueError):
      await sorter.sort_to_plate(cells_per_well=0, wells=96, template="t")
    with self.assertRaises(ValueError):
      await sorter.sort_to_plate(cells_per_well=1, wells=0, template="t")
    with self.assertRaises(ValueError):
      await sorter.sort_to_plate(cells_per_well=1, wells=96, template="t", plate_format="6")

  async def test_chatterbox_backend_reports_idle(self):
    sorter = CellSorter(backend=CellSorterChatterboxBackend())
    await sorter._on_setup()
    self.assertEqual(await sorter.get_status(), "idle")
    await sorter.sort_to_plate(cells_per_well=1, wells=4, template="t")


if __name__ == "__main__":
  unittest.main()
