"""Single-tube barcode-scanning backend for the Micronic Code Reader IO Monitor server."""

from __future__ import annotations

from typing import Optional

from pylabrobot.capabilities.barcode_scanning import BarcodeScannerBackend, BarcodeScannerError
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources.barcode import Barcode

from .driver import MicronicError, MicronicIOMonitorDriver


class MicronicBarcodeScannerError(MicronicError, BarcodeScannerError):
  """Raised when Micronic single-tube barcode scanning fails."""


class MicronicIOMonitorBarcodeScannerBackend(BarcodeScannerBackend):
  """Single-tube barcode-scanning backend for the Micronic Code Reader IO Monitor server."""

  def __init__(
    self,
    driver: MicronicIOMonitorDriver,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
  ):
    super().__init__()
    self.driver = driver
    self.timeout = timeout
    self.poll_interval = poll_interval

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    try:
      await self.driver.get_iomonitor_state()
    except MicronicError as exc:
      raise MicronicBarcodeScannerError(str(exc)) from exc

  async def scan_barcode(self) -> Barcode:
    try:
      initial_state = await self.driver.get_iomonitor_state()
      await self.driver.request(
        "POST",
        "/scantube",
        data=b"",
        headers=None,
        expect_json=False,
      )
      await self.driver.wait_for_fresh_data_ready(
        initial_state=initial_state,
        timeout=self.timeout,
        poll_interval=self.poll_interval,
      )
      data = await self.driver.get_single_tube_barcode()
    except MicronicError as exc:
      raise MicronicBarcodeScannerError(str(exc)) from exc
    return Barcode(data=data, symbology="Data Matrix", position_on_resource="bottom")
