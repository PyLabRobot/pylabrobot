"""Tests for InstrumentStatus."""

import unittest

from pylabrobot.capabilities.scanning.instrument_status.backend import (
  InstrumentStatusBackend,
)
from pylabrobot.capabilities.scanning.instrument_status.instrument_status import (
  InstrumentStatus,
)
from pylabrobot.capabilities.scanning.instrument_status.standard import (
  InstrumentStatusReading,
)


class StubInstrumentStatusBackend(InstrumentStatusBackend):
  """Backend that returns a fixed reading and counts calls."""

  def __init__(self, reading: InstrumentStatusReading):
    self.reading = reading
    self.call_count = 0

  async def read_status(self) -> InstrumentStatusReading:
    self.call_count += 1
    return self.reading


class TestInstrumentStatus(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.reading = InstrumentStatusReading(
      state="Scanning",
      current_user="alice",
      progress=37.5,
      time_remaining="2 minutes",
      lid_open=False,
    )
    self.backend = StubInstrumentStatusBackend(self.reading)
    self.cap = InstrumentStatus(backend=self.backend)
    await self.cap._on_setup()

  async def test_read_status_returns_backend_reading(self):
    result = await self.cap.read_status()
    self.assertIs(result, self.reading)
    self.assertEqual(self.backend.call_count, 1)

  async def test_read_status_passes_through_fields(self):
    result = await self.cap.read_status()
    self.assertEqual(result.state, "Scanning")
    self.assertEqual(result.current_user, "alice")
    self.assertEqual(result.progress, 37.5)
    self.assertEqual(result.time_remaining, "2 minutes")
    self.assertFalse(result.lid_open)

  async def test_read_status_requires_setup(self):
    backend = StubInstrumentStatusBackend(self.reading)
    cap = InstrumentStatus(backend=backend)
    with self.assertRaises(RuntimeError):
      await cap.read_status()
    self.assertEqual(backend.call_count, 0)


if __name__ == "__main__":
  unittest.main()
