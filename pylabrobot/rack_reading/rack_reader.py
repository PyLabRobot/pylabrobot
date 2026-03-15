from __future__ import annotations

import asyncio
from typing import List

from pylabrobot.machines.machine import Machine, need_setup_finished

from .backend import RackReaderBackend
from .standard import LayoutInfo, RackReaderState, RackReaderTimeoutError, RackScanResult


class RackReader(Machine):
  """Frontend for rack readers that decode position-indexed rack contents."""

  def __init__(self, backend: RackReaderBackend) -> None:
    super().__init__(backend=backend)
    self.backend: RackReaderBackend = backend

  @need_setup_finished
  async def get_state(self) -> RackReaderState:
    return await self.backend.get_state()

  @need_setup_finished
  async def scan_box(self) -> None:
    await self.backend.scan_box()

  @need_setup_finished
  async def scan_tube(self) -> None:
    await self.backend.scan_tube()

  @need_setup_finished
  async def get_scan_result(self) -> RackScanResult:
    return await self.backend.get_scan_result()

  @need_setup_finished
  async def get_rack_id(self) -> str:
    return await self.backend.get_rack_id()

  @need_setup_finished
  async def get_layouts(self) -> List[LayoutInfo]:
    return await self.backend.get_layouts()

  @need_setup_finished
  async def get_current_layout(self) -> str:
    return await self.backend.get_current_layout()

  @need_setup_finished
  async def set_current_layout(self, layout: str) -> None:
    await self.backend.set_current_layout(layout)

  @need_setup_finished
  async def wait_for_data_ready(
    self, timeout: float = 60.0, poll_interval: float = 2.0
  ) -> RackReaderState:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
      state = await self.backend.get_state()
      if state == RackReaderState.DATAREADY:
        return state

      if loop.time() >= deadline:
        raise RackReaderTimeoutError(
          f"Timed out waiting for rack reader to reach {RackReaderState.DATAREADY.value}."
        )

      await asyncio.sleep(poll_interval)

  @need_setup_finished
  async def scan_box_and_wait(
    self, timeout: float = 60.0, poll_interval: float = 2.0
  ) -> RackScanResult:
    await self.backend.scan_box()
    await self.wait_for_data_ready(timeout=timeout, poll_interval=poll_interval)
    return await self.backend.get_scan_result()
