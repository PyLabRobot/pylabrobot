from __future__ import annotations

from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.tube_rack import TubeRack

from .backend import RackReaderBackend
from .standard import RackScanEntry, RackScanResult


class RackReaderChatterboxBackend(RackReaderBackend):
  """Device-free rack-reading backend for tests and examples."""

  async def scan_rack(self, rack: TubeRack, timeout: float, poll_interval: float) -> RackScanResult:
    del rack, timeout, poll_interval
    return RackScanResult(
      rack_id="CHATTERBOX",
      entries=[
        RackScanEntry(
          position="A1",
          tube_id="SIMULATED",
          status="OK",
          barcode=Barcode(data="SIMULATED", symbology="DataMatrix", position_on_resource="bottom"),
        ),
      ],
      rack_barcode=Barcode(
        data="CHATTERBOX", symbology="Code 128 (Subset B and C)", position_on_resource="right"
      ),
    )

  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    del timeout, poll_interval
    return "CHATTERBOX"
