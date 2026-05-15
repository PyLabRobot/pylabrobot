import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.capabilities.rack_reading.rack_reader import RackReader
from pylabrobot.capabilities.rack_reading.standard import RackScanEntry, RackScanResult
from pylabrobot.resources.tube_rack import TubeRack

SCAN_RESULT = RackScanResult(
  rack_id="5500135415",
  entries=[
    RackScanEntry(position="A01", tube_id="7518613629", status="OK"),
  ],
)


def _make_backend() -> MagicMock:
  backend = MagicMock()
  backend.scan_rack = AsyncMock(return_value=SCAN_RESULT)
  backend.scan_rack_id = AsyncMock(return_value=SCAN_RESULT.rack_id)
  backend._on_setup = AsyncMock()
  backend._on_stop = AsyncMock()
  return backend


class TestRackReader(unittest.IsolatedAsyncioTestCase):
  async def test_scan_rack_delegates_to_backend(self):
    backend = _make_backend()
    rack = MagicMock(spec=TubeRack)
    reader = RackReader(backend=backend)
    await reader._on_setup()

    result = await reader.scan_rack(rack=rack, timeout=1.0, poll_interval=0.01)

    self.assertEqual(result, SCAN_RESULT)
    backend.scan_rack.assert_awaited_once_with(rack=rack, timeout=1.0, poll_interval=0.01)

  async def test_scan_rack_id_delegates_to_backend(self):
    backend = _make_backend()
    reader = RackReader(backend=backend)
    await reader._on_setup()

    rack_id = await reader.scan_rack_id(timeout=1.0, poll_interval=0.01)

    self.assertEqual(rack_id, SCAN_RESULT.rack_id)
    backend.scan_rack_id.assert_awaited_once_with(timeout=1.0, poll_interval=0.01)

  async def test_requires_setup(self):
    backend = _make_backend()
    rack = MagicMock(spec=TubeRack)
    reader = RackReader(backend=backend)

    with self.assertRaises(RuntimeError):
      await reader.scan_rack(rack=rack)


if __name__ == "__main__":
  unittest.main()
