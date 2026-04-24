from __future__ import annotations

from .backend import RackReaderBackend
from .standard import LayoutInfo, RackReaderState, RackScanEntry, RackScanResult


class RackReaderChatterboxBackend(RackReaderBackend):
  """Device-free rack-reading backend for tests and examples."""

  def __init__(self):
    self._state = RackReaderState.IDLE
    self._layout = "96"

  async def get_state(self) -> RackReaderState:
    return self._state

  async def trigger_rack_scan(self) -> None:
    self._state = RackReaderState.DATAREADY

  async def trigger_rack_id_scan(self) -> None:
    self._state = RackReaderState.DATAREADY

  async def get_scan_result(self) -> RackScanResult:
    return RackScanResult(
      rack_id="CHATTERBOX",
      date="19700101",
      time="000000",
      entries=[
        RackScanEntry(position="A01", tube_id="SIMULATED", status="Code OK"),
      ],
    )

  async def get_rack_id(self) -> str:
    return "CHATTERBOX"

  async def get_layouts(self) -> list[LayoutInfo]:
    return [LayoutInfo(name="96"), LayoutInfo(name="48")]

  async def get_current_layout(self) -> str:
    return self._layout

  async def set_current_layout(self, layout: str) -> None:
    self._layout = layout
