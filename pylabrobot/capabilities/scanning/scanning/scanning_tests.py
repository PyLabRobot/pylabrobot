"""Tests for Scanning."""

import unittest
from typing import List, Optional, Tuple

from pylabrobot.capabilities.scanning.scanning.backend import ScanningBackend
from pylabrobot.capabilities.scanning.scanning.scanning import Scanning
from pylabrobot.serializer import SerializableMixin


class RecordingScanningBackend(ScanningBackend):
  """Backend that records every call so tests can assert on the sequence."""

  def __init__(self):
    self.calls: List[Tuple[str, Optional[SerializableMixin]]] = []

  async def configure(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    self.calls.append(("configure", backend_params))

  async def start(self) -> None:
    self.calls.append(("start", None))

  async def stop(self) -> None:
    self.calls.append(("stop", None))

  async def pause(self) -> None:
    self.calls.append(("pause", None))

  async def cancel(self) -> None:
    self.calls.append(("cancel", None))


class TestScanning(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = RecordingScanningBackend()
    self.cap = Scanning(backend=self.backend)
    await self.cap._on_setup()

  async def test_configure_forwards_params(self):
    sentinel = object()
    await self.cap.configure(backend_params=sentinel)  # type: ignore[arg-type]
    self.assertEqual(self.backend.calls, [("configure", sentinel)])

  async def test_full_verb_sequence(self):
    await self.cap.configure()
    await self.cap.start()
    await self.cap.pause()
    await self.cap.cancel()
    await self.cap.stop()
    self.assertEqual(
      [name for name, _ in self.backend.calls],
      ["configure", "start", "pause", "cancel", "stop"],
    )

  async def test_setup_finished_flag(self):
    self.assertTrue(self.cap.setup_finished)
    await self.cap._on_stop()
    self.assertFalse(self.cap.setup_finished)

  async def test_methods_require_setup(self):
    backend = RecordingScanningBackend()
    cap = Scanning(backend=backend)
    with self.assertRaises(RuntimeError):
      await cap.start()
    with self.assertRaises(RuntimeError):
      await cap.configure()
    self.assertEqual(backend.calls, [])


if __name__ == "__main__":
  unittest.main()
