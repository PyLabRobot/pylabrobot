import unittest
from unittest.mock import AsyncMock

from pylabrobot.rack_reading.backend import RackReaderBackend
from pylabrobot.rack_reading.rack_reader import RackReader
from pylabrobot.rack_reading.standard import (
  LayoutInfo,
  RackReaderState,
  RackReaderTimeoutError,
  RackScanEntry,
  RackScanResult,
)


class MockRackReaderBackend(RackReaderBackend):
  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def get_state(self) -> RackReaderState:
    return RackReaderState.IDLE

  async def scan_box(self) -> None:
    pass

  async def scan_tube(self) -> None:
    pass

  async def get_scan_result(self) -> RackScanResult:
    return RackScanResult(
      rack_id="rack",
      date="20260315",
      time="114804",
      entries=[RackScanEntry(position="A01", tube_id="tube", status="Code OK")],
    )

  async def get_rack_id(self) -> str:
    return "rack"

  async def get_layouts(self) -> list[LayoutInfo]:
    return [LayoutInfo(name="96")]

  async def get_current_layout(self) -> str:
    return "96"

  async def set_current_layout(self, layout: str) -> None:
    return None


class TestRackReader(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.backend = AsyncMock(spec=MockRackReaderBackend)
    self.reader = RackReader(backend=self.backend)
    await self.reader.setup()

  async def test_scan_box_and_wait(self):
    self.backend.get_state.side_effect = [
      RackReaderState.SCANNING,
      RackReaderState.DATAREADY,
    ]
    expected = RackScanResult(
      rack_id="rack",
      date="20260315",
      time="114804",
      entries=[RackScanEntry(position="A01", tube_id="tube", status="Code OK")],
    )
    self.backend.get_scan_result.return_value = expected

    result = await self.reader.scan_box_and_wait(timeout=1.0, poll_interval=0.001)

    self.backend.scan_box.assert_called_once()
    self.assertEqual(result, expected)

  async def test_wait_for_data_ready_timeout(self):
    self.backend.get_state.return_value = RackReaderState.SCANNING

    with self.assertRaises(RackReaderTimeoutError):
      await self.reader.wait_for_data_ready(timeout=0.01, poll_interval=0.001)

  async def test_get_rack_id(self):
    self.backend.get_rack_id.return_value = "3000756455"

    rack_id = await self.reader.get_rack_id()

    self.assertEqual(rack_id, "3000756455")
