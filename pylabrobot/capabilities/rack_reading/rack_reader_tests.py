import unittest

from pylabrobot.capabilities.rack_reading.backend import RackReaderBackend
from pylabrobot.capabilities.rack_reading.rack_reader import RackReader
from pylabrobot.capabilities.rack_reading.standard import (
  LayoutInfo,
  RackReaderState,
  RackReaderTimeoutError,
  RackScanEntry,
  RackScanResult,
)


class RecordingRackReaderBackend(RackReaderBackend):
  def __init__(self):
    self.state = RackReaderState.IDLE
    self.calls: list[str] = []
    self.result = RackScanResult(
      rack_id="5500135415",
      date="20260316",
      time="160626",
      entries=[
        RackScanEntry(position="A01", tube_id="7518613629", status="Code OK", free_text=""),
      ],
    )

  async def get_state(self) -> RackReaderState:
    self.calls.append("get_state")
    if self.state == RackReaderState.SCANNING:
      self.state = RackReaderState.DATAREADY
    return self.state

  async def trigger_rack_scan(self) -> None:
    self.calls.append("trigger_rack_scan")
    self.state = RackReaderState.SCANNING

  async def get_scan_result(self) -> RackScanResult:
    self.calls.append("get_scan_result")
    return self.result

  async def get_rack_id(self) -> str:
    self.calls.append("get_rack_id")
    return self.result.rack_id

  async def get_layouts(self) -> list[LayoutInfo]:
    self.calls.append("get_layouts")
    return [LayoutInfo(name="96")]

  async def get_current_layout(self) -> str:
    self.calls.append("get_current_layout")
    return "96"

  async def set_current_layout(self, layout: str) -> None:
    self.calls.append(f"set_current_layout:{layout}")


class StuckRackReaderBackend(RecordingRackReaderBackend):
  async def get_state(self) -> RackReaderState:
    self.calls.append("get_state")
    return RackReaderState.SCANNING


class StaleDataReadyRackReaderBackend(RecordingRackReaderBackend):
  def __init__(self):
    super().__init__()
    self.state = RackReaderState.DATAREADY
    self._states_after_trigger = [
      RackReaderState.DATAREADY,
      RackReaderState.SCANNING,
      RackReaderState.DATAREADY,
    ]

  async def trigger_rack_scan(self) -> None:
    self.calls.append("trigger_rack_scan")

  async def get_state(self) -> RackReaderState:
    self.calls.append("get_state")
    if self._states_after_trigger:
      return self._states_after_trigger.pop(0)
    return RackReaderState.DATAREADY


class TestRackReader(unittest.IsolatedAsyncioTestCase):
  async def test_scan_rack_triggers_and_returns_result(self):
    backend = RecordingRackReaderBackend()
    reader = RackReader(backend=backend)
    await reader._on_setup()

    result = await reader.scan_rack(timeout=1.0, poll_interval=0.01)

    self.assertEqual(result.rack_id, "5500135415")
    self.assertEqual(result.entries[0].position, "A01")
    self.assertEqual(
      backend.calls[:3],
      ["get_state", "trigger_rack_scan", "get_state"],
    )

  async def test_scan_rack_waits_for_new_dataready_cycle(self):
    backend = StaleDataReadyRackReaderBackend()
    reader = RackReader(backend=backend)
    await reader._on_setup()

    result = await reader.scan_rack(timeout=1.0, poll_interval=0.0)

    self.assertEqual(result.rack_id, "5500135415")
    self.assertEqual(
      backend.calls,
      ["get_state", "trigger_rack_scan", "get_state", "get_state", "get_scan_result"],
    )

  async def test_scan_rack_times_out(self):
    backend = StuckRackReaderBackend()
    reader = RackReader(backend=backend)
    await reader._on_setup()

    with self.assertRaises(RackReaderTimeoutError):
      await reader.scan_rack(timeout=0.01, poll_interval=0.0)

  async def test_requires_setup(self):
    backend = RecordingRackReaderBackend()
    reader = RackReader(backend=backend)

    with self.assertRaises(RuntimeError):
      await reader.get_state()


if __name__ == "__main__":
  unittest.main()
