from __future__ import annotations

import asyncio
import time

from pylabrobot.capabilities.capability import Capability, need_capability_ready

from .backend import RackReaderBackend
from .standard import LayoutInfo, RackReaderState, RackReaderTimeoutError, RackScanResult


class RackReader(Capability):
  """Rack-reading capability."""

  def __init__(self, backend: RackReaderBackend):
    super().__init__(backend=backend)
    self.backend: RackReaderBackend = backend

  @need_capability_ready
  async def get_state(self) -> RackReaderState:
    return await self.backend.get_state()

  @need_capability_ready
  async def trigger_rack_scan(self) -> None:
    await self.backend.trigger_rack_scan()

  @need_capability_ready
  async def trigger_tube_scan(self) -> None:
    await self.backend.trigger_tube_scan()

  @need_capability_ready
  async def get_scan_result(self) -> RackScanResult:
    return await self.backend.get_scan_result()

  @need_capability_ready
  async def get_rack_id(self) -> str:
    return await self.backend.get_rack_id()

  @need_capability_ready
  async def get_layouts(self) -> list[LayoutInfo]:
    return await self.backend.get_layouts()

  @need_capability_ready
  async def get_current_layout(self) -> str:
    return await self.backend.get_current_layout()

  @need_capability_ready
  async def set_current_layout(self, layout: str) -> None:
    await self.backend.set_current_layout(layout)

  async def _wait_for_state(
    self,
    target: RackReaderState,
    timeout: float,
    poll_interval: float,
  ) -> RackReaderState:
    deadline = time.monotonic() + timeout
    while True:
      state = await self.backend.get_state()
      if state == target:
        return state
      if time.monotonic() >= deadline:
        raise RackReaderTimeoutError(
          f"Timed out waiting for rack reader to reach {target.value}."
        )
      await asyncio.sleep(poll_interval)

  @need_capability_ready
  async def scan_rack(
    self,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
  ) -> RackScanResult:
    """Trigger a rack scan and return the completed result."""

    await self.backend.trigger_rack_scan()
    await self._wait_for_state(
      target=RackReaderState.DATAREADY,
      timeout=timeout,
      poll_interval=poll_interval,
    )
    return await self.backend.get_scan_result()
