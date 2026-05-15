from __future__ import annotations

from pylabrobot.resources.tube_rack import TubeRack

from .backend import RackReaderBackend
from .standard import RackScanEntry, RackScanResult


class RackReaderChatterboxBackend(RackReaderBackend):
  """Device-free rack-reading backend for tests and examples."""

  async def scan_rack(self, rack: TubeRack, timeout: float, poll_interval: float) -> RackScanResult:
    del rack, timeout, poll_interval
    return RackScanResult(
      rack_id="CHATTERBOX",
      date="19700101",
      time="000000",
      entries=[
        RackScanEntry(position="A01", tube_id="SIMULATED", status="Code OK"),
      ],
    )

  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    del timeout, poll_interval
    return "CHATTERBOX"
