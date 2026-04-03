from __future__ import annotations

from typing import Optional

from pylabrobot.capabilities.rack_reading import RackReader
from pylabrobot.device import Device

from .http_driver import MicronicHTTPDriver
from .rack_reading_backend import MicronicRackReadingBackend


class MicronicCodeReader(Device):
  """Micronic Code Reader device using the IO Monitor HTTP server."""

  def __init__(
    self,
    host: str = "localhost",
    port: int = 2500,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    driver: Optional[MicronicHTTPDriver] = None,
  ):
    if driver is None:
      driver = MicronicHTTPDriver(host=host, port=port, timeout=timeout)
    super().__init__(driver=driver)
    self.driver: MicronicHTTPDriver = driver
    self.default_timeout = timeout
    self.default_poll_interval = poll_interval
    self.rack_reading = RackReader(backend=MicronicRackReadingBackend(driver))
    # Temporary alias while the consumer code moves to the capability-centric v1b1 surface.
    self.rack_reader = self.rack_reading
    self._capabilities = [self.rack_reading]

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "timeout": self.default_timeout,
      "poll_interval": self.default_poll_interval,
    }
