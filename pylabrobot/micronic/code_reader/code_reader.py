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
    timeout: float = 90.0,
    poll_interval: float = 1.0,
    serial_timeout_ms: int = 2500,
    keep_images: bool = False,
    rack_id_override: Optional[str] = None,
  ):
    driver = MicronicDriver(
      scanner=scanner,
      serial_port=serial_port,
      image_dir=image_dir,
      scanner_timeout_ms=int(timeout * 1000),
      serial_timeout_ms=serial_timeout_ms,
      keep_images=keep_images,
      rack_id_override=rack_id_override,
    )
    super().__init__(driver=driver)
    self.driver: MicronicDriver = driver
    self.default_timeout = timeout
    self.default_poll_interval = poll_interval
    self.rack_reading = RackReader(backend=MicronicRackReadingBackend(driver))
    self._capabilities = [self.rack_reading]

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "timeout": self.default_timeout,
      "poll_interval": self.default_poll_interval,
    }
