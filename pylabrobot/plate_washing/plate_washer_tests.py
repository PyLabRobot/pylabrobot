"""Tests for PlateWasher frontend."""

import unittest

from pylabrobot.plate_washing.backend import PlateWasherBackend
from pylabrobot.plate_washing.plate_washer import PlateWasher


class MockPlateWasherBackend(PlateWasherBackend):
  """A minimal mock backend for testing the PlateWasher frontend."""

  def __init__(self):
    super().__init__()
    self.setup_called = False
    self.stop_called = False

  async def setup(self) -> None:
    self.setup_called = True

  async def stop(self) -> None:
    self.stop_called = True


class TestPlateWasherSetup(unittest.IsolatedAsyncioTestCase):
  """Test PlateWasher setup and teardown."""

  def setUp(self) -> None:
    self.backend = MockPlateWasherBackend()
    self.washer = PlateWasher(
      name="test_washer",
      size_x=200.0,
      size_y=200.0,
      size_z=100.0,
      backend=self.backend,
    )

  async def test_setup_calls_backend_setup(self):
    """Setup should call backend.setup()."""
    await self.washer.setup()
    self.assertTrue(self.backend.setup_called)

  async def test_setup_finished_after_setup(self):
    """setup_finished should be True after setup()."""
    self.assertFalse(self.washer.setup_finished)
    await self.washer.setup()
    self.assertTrue(self.washer.setup_finished)

  async def test_stop_calls_backend_stop(self):
    """Stop should call backend.stop()."""
    await self.washer.setup()
    await self.washer.stop()
    self.assertTrue(self.backend.stop_called)

  async def test_context_manager(self):
    """PlateWasher should work as async context manager."""
    async with self.washer:
      self.assertTrue(self.backend.setup_called)
    self.assertTrue(self.backend.stop_called)


class TestPlateWasherSerialization(unittest.TestCase):
  """Test PlateWasher serialization."""

  def test_backend_serialization(self):
    """Backend should serialize correctly."""
    backend = MockPlateWasherBackend()
    serialized = backend.serialize()
    self.assertEqual(serialized["type"], "MockPlateWasherBackend")


if __name__ == "__main__":
  unittest.main()
