"""Micronic Code Reader device."""

from __future__ import annotations

from typing import Optional

from pylabrobot.capabilities.rack_reading import RackReader
from pylabrobot.device import Device

from .driver import MicronicDriver
from .rack_reading_backend import MicronicRackReadingBackend
from .scanner import Scanner


class MicronicCodeReader(Device):
  """Micronic rack reader device.

  The rack-reading capability is driven by ``MicronicDriver``.
  """

  def __init__(
    self,
    scanner: Scanner,
    serial_port: str,
    image_dir: Optional[str] = None,
    scanner_timeout: float = 90.0,
    serial_timeout_ms: int = 2500,
    keep_images: bool = False,
  ):
    driver = MicronicDriver(
      scanner=scanner,
      serial_port=serial_port,
      image_dir=image_dir,
      scanner_timeout_ms=int(scanner_timeout * 1000),
      serial_timeout_ms=serial_timeout_ms,
      keep_images=keep_images,
    )
    super().__init__(driver=driver)
    self.driver: MicronicDriver = driver
    self.rack_reading = RackReader(backend=MicronicRackReadingBackend(driver))
    self._capabilities = [self.rack_reading]
