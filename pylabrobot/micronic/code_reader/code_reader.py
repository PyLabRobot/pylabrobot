"""Micronic Code Reader device."""

from __future__ import annotations

from typing import Optional, Sequence

from pylabrobot.capabilities.rack_reading import RackReader
from pylabrobot.device import Device

from .driver import MicronicDriver
from .rack_reading_backend import MicronicRackReadingBackend


class MicronicCodeReader(Device):
  """Micronic rack reader device.

  The rack-reading capability is driven by ``MicronicDriver``.
  """

  def __init__(
    self,
    twain_scanner_path: Optional[str] = None,
    twain_source: str = "AVA6PlusG",
    sane_device: Optional[str] = None,
    scanner_backend: str = "auto",
    scan_command: Optional[Sequence[str]] = None,
    image_extension: Optional[str] = None,
    image_dir: Optional[str] = None,
    serial_port: str = "COM4",
    rack_id_command: Optional[Sequence[str]] = None,
    timeout: float = 90.0,
    poll_interval: float = 1.0,
    serial_timeout_ms: int = 2500,
    keep_images: bool = False,
    image_input: Optional[str] = None,
    rack_id_override: Optional[str] = None,
    driver: Optional[MicronicDriver] = None,
  ):
    if driver is None:
      driver = MicronicDriver(
        twain_scanner_path=twain_scanner_path,
        twain_source=twain_source,
        sane_device=sane_device,
        scanner_backend=scanner_backend,
        scan_command=scan_command,
        image_extension=image_extension,
        image_dir=image_dir,
        serial_port=serial_port,
        rack_id_command=rack_id_command,
        scanner_timeout_ms=int(timeout * 1000),
        serial_timeout_ms=serial_timeout_ms,
        keep_images=keep_images,
        image_input=image_input,
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
