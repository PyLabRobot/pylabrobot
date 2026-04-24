"""Micronic Code Reader device."""

from __future__ import annotations

from typing import Optional

from pylabrobot.capabilities.barcode_scanning import BarcodeScanner
from pylabrobot.capabilities.rack_reading import RackReader
from pylabrobot.device import Device

from .barcode_scanning_backend import MicronicIOMonitorBarcodeScannerBackend
from .driver import MicronicIOMonitorDriver
from .rack_reading_backend import MicronicIOMonitorRackReadingBackend


class MicronicCodeReader(Device):
  """Micronic Code Reader device using the IO Monitor HTTP server."""

  def __init__(
    self,
    host: str = "localhost",
    port: int = 2500,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    driver: Optional[MicronicIOMonitorDriver] = None,
  ):
    if driver is None:
      driver = MicronicIOMonitorDriver(host=host, port=port, timeout=timeout)
    super().__init__(driver=driver)
    self.driver: MicronicIOMonitorDriver = driver
    self.default_timeout = timeout
    self.default_poll_interval = poll_interval
    self.rack_reading = RackReader(backend=MicronicIOMonitorRackReadingBackend(driver))
    self.barcode_scanning = BarcodeScanner(
      backend=MicronicIOMonitorBarcodeScannerBackend(
        driver,
        timeout=timeout,
        poll_interval=poll_interval,
      )
    )
    self._capabilities = [self.rack_reading, self.barcode_scanning]

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "timeout": self.default_timeout,
      "poll_interval": self.default_poll_interval,
    }
