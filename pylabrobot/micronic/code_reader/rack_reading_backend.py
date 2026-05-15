"""Rack-reading backend for the Micronic driver."""

from __future__ import annotations

import asyncio
import time

from pylabrobot.capabilities.rack_reading import (
  RackReaderBackend,
  RackReaderError,
  RackScanResult,
)
from pylabrobot.resources.tube_rack import TubeRack

from .driver import MicronicDriver, MicronicError, MicronicRackReaderState


class MicronicRackReaderError(MicronicError, RackReaderError):
  """Raised when Micronic rack-reading operations fail."""


class MicronicRackReadingBackend(RackReaderBackend):
  """Rack-reading backend that delegates to the Micronic driver."""

  def __init__(self, driver: MicronicDriver):
    super().__init__()
    self.driver = driver

  async def scan_rack(self, rack: TubeRack, timeout: float, poll_interval: float) -> RackScanResult:
    try:
      initial_state = await self.driver.get_rack_reader_state()
      await self.driver.trigger_rack_scan(rack)
      await self._wait_for_fresh_dataready(
        initial_state=initial_state, timeout=timeout, poll_interval=poll_interval
      )
      return await self.driver.get_scan_result()
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    try:
      return await self.driver.scan_rack_id(timeout=timeout, poll_interval=poll_interval)
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def _wait_for_fresh_dataready(
    self,
    initial_state: MicronicRackReaderState,
    timeout: float,
    poll_interval: float,
  ) -> None:
    require_state_change = initial_state == MicronicRackReaderState.DATAREADY
    deadline = time.monotonic() + timeout
    while True:
      state = await self.driver.get_rack_reader_state()
      if state != MicronicRackReaderState.DATAREADY:
        require_state_change = False
      elif not require_state_change:
        return
      if time.monotonic() >= deadline:
        raise TimeoutError("Timed out waiting for Micronic rack reader to reach dataready.")
      await asyncio.sleep(poll_interval)
