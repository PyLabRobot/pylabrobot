import unittest
from unittest.mock import AsyncMock, Mock, call

from pylabrobot.capabilities.rack_reading.backend import RackReaderBackend
from pylabrobot.capabilities.rack_reading.rack_reader import RackReader
from pylabrobot.capabilities.rack_reading.standard import (
  RackReaderState,
  RackReaderTimeoutError,
  RackScanEntry,
  RackScanResult,
)


SCAN_RESULT = RackScanResult(
  rack_id="5500135415",
  date="20260316",
  time="160626",
  entries=[
    RackScanEntry(position="A01", tube_id="7518613629", status="Code OK", free_text=""),
  ],
)


def _make_backend(get_state_side_effect=None) -> Mock:
  backend = Mock(spec=RackReaderBackend)
  backend.get_state = AsyncMock(side_effect=get_state_side_effect)
  backend.trigger_rack_scan = AsyncMock()
  backend.scan_rack_id = AsyncMock(return_value=SCAN_RESULT.rack_id)
  backend.get_scan_result = AsyncMock(return_value=SCAN_RESULT)
  backend.get_rack_id = AsyncMock(return_value=SCAN_RESULT.rack_id)
  return backend


class TestRackReader(unittest.IsolatedAsyncioTestCase):
  async def test_scan_rack_triggers_and_returns_result(self):
    backend = _make_backend(
      get_state_side_effect=[RackReaderState.IDLE, RackReaderState.DATAREADY],
    )
    reader = RackReader(backend=backend)
    await reader._on_setup()

    backend.reset_mock()
    result = await reader.scan_rack(timeout=1.0, poll_interval=0.01)

    self.assertEqual(result, SCAN_RESULT)
    self.assertEqual(
      backend.mock_calls[:3],
      [call.get_state(), call.trigger_rack_scan(), call.get_state()],
    )

  async def test_scan_rack_waits_for_new_dataready_cycle(self):
    backend = _make_backend(
      get_state_side_effect=[
        RackReaderState.DATAREADY,
        RackReaderState.SCANNING,
        RackReaderState.DATAREADY,
      ],
    )
    reader = RackReader(backend=backend)
    await reader._on_setup()

    backend.reset_mock()
    result = await reader.scan_rack(timeout=1.0, poll_interval=0.0)

    self.assertEqual(result, SCAN_RESULT)
    self.assertEqual(
      backend.mock_calls,
      [
        call.get_state(),
        call.trigger_rack_scan(),
        call.get_state(),
        call.get_state(),
        call.get_scan_result(),
      ],
    )

  async def test_scan_rack_id_delegates_to_backend(self):
    backend = _make_backend()
    reader = RackReader(backend=backend)
    await reader._on_setup()

    rack_id = await reader.scan_rack_id(timeout=1.0, poll_interval=0.01)

    self.assertEqual(rack_id, SCAN_RESULT.rack_id)
    backend.scan_rack_id.assert_awaited_once_with(timeout=1.0, poll_interval=0.01)

  async def test_scan_rack_times_out(self):
    backend = _make_backend()
    backend.get_state.return_value = RackReaderState.SCANNING
    reader = RackReader(backend=backend)
    await reader._on_setup()

    with self.assertRaises(RackReaderTimeoutError):
      await reader.scan_rack(timeout=0.01, poll_interval=0.0)

  async def test_requires_setup(self):
    backend = _make_backend()
    reader = RackReader(backend=backend)

    with self.assertRaises(RuntimeError):
      await reader.get_state()


if __name__ == "__main__":
  unittest.main()
