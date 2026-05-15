from __future__ import annotations

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.resources.tube_rack import TubeRack

from .backend import RackReaderBackend
from .standard import RackScanResult


class RackReader(Capability):
  """Rack-reading capability."""

  def __init__(self, backend: RackReaderBackend):
    super().__init__(backend=backend)
    self.backend: RackReaderBackend = backend

  @need_capability_ready
  async def scan_rack(
    self,
    rack: TubeRack,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
  ) -> RackScanResult:
    return await self.backend.scan_rack(rack=rack, timeout=timeout, poll_interval=poll_interval)

  @need_capability_ready
  async def scan_rack_id(
    self,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
  ) -> str:
    return await self.backend.scan_rack_id(timeout=timeout, poll_interval=poll_interval)
