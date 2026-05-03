"""Tests for ImageRetrieval."""

import unittest
from typing import Dict, List

from pylabrobot.capabilities.scanning.image_retrieval.backend import (
  ImageRetrievalBackend,
)
from pylabrobot.capabilities.scanning.image_retrieval.image_retrieval import (
  ImageRetrieval,
)


class InMemoryImageRetrievalBackend(ImageRetrievalBackend):
  """Backend that serves a pre-loaded in-memory store."""

  def __init__(self, store: Dict[str, Dict[str, bytes]]):
    self._store = store

  async def list_groups(self) -> List[str]:
    return list(self._store.keys())

  async def list_scans(self, group: str) -> List[str]:
    return list(self._store.get(group, {}).keys())

  async def download(self, group: str, scan_name: str) -> bytes:
    return self._store[group][scan_name]


class TestImageRetrieval(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.store = {
      "odyssey": {"scan_a": b"AAAA", "scan_b": b"BBBB"},
      "public": {"shared": b"CCCC"},
    }
    self.backend = InMemoryImageRetrievalBackend(self.store)
    self.cap = ImageRetrieval(backend=self.backend)
    await self.cap._on_setup()

  async def test_list_groups(self):
    self.assertEqual(sorted(await self.cap.list_groups()), ["odyssey", "public"])

  async def test_list_scans(self):
    self.assertEqual(
      sorted(await self.cap.list_scans("odyssey")), ["scan_a", "scan_b"],
    )
    self.assertEqual(await self.cap.list_scans("missing"), [])

  async def test_download(self):
    self.assertEqual(await self.cap.download("odyssey", "scan_a"), b"AAAA")
    self.assertEqual(await self.cap.download("public", "shared"), b"CCCC")

  async def test_methods_require_setup(self):
    backend = InMemoryImageRetrievalBackend({})
    cap = ImageRetrieval(backend=backend)
    with self.assertRaises(RuntimeError):
      await cap.list_groups()
    with self.assertRaises(RuntimeError):
      await cap.download("g", "s")


if __name__ == "__main__":
  unittest.main()
