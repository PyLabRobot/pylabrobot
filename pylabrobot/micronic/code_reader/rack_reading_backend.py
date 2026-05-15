"""Rack-reading backend for the Micronic driver."""

from __future__ import annotations

import asyncio

from pylabrobot.capabilities.rack_reading import RackReaderBackend, RackScanResult
from pylabrobot.resources.tube_rack import TubeRack

from .driver import MicronicDriver


class MicronicRackReadingBackend(RackReaderBackend):
  """Rack-reading backend that delegates to the Micronic driver."""

  def __init__(self, driver: MicronicDriver):
    super().__init__()
    self.driver = driver

  async def scan_rack(self, rack: TubeRack, timeout: float, poll_interval: float) -> RackScanResult:
    del poll_interval
    return await asyncio.wait_for(self.driver.scan_rack(rack), timeout=timeout)

  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    return await self.driver.scan_rack_id(timeout=timeout, poll_interval=poll_interval)
