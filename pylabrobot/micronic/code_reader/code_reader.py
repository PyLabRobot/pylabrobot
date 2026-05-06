"""Micronic Code Reader device."""

from __future__ import annotations

from typing import Optional, Sequence

from pylabrobot.capabilities.barcode_scanning import BarcodeScanner
from pylabrobot.capabilities.rack_reading import RackReader
from pylabrobot.device import Device

from .barcode_scanning_backend import MicronicIOMonitorBarcodeScannerBackend
from .direct_driver import MicronicDirectDriver
from .driver import MicronicIOMonitorDriver, MicronicRackReaderDriver
from .rack_reading_backend import MicronicRackReadingBackend


class MicronicCodeReader(Device):
  """Micronic rack reader device.

  The rack-reading capability is driven by ``driver``. By default this uses the
  Micronic IO Monitor HTTP server, but a ``MicronicDirectDriver`` can be supplied
  to control the local scanner hardware directly.
  """

  def __init__(
    self,
    host: str = "localhost",
    port: int = 2500,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    driver: Optional[MicronicRackReaderDriver] = None,
  ):
    if driver is None:
      driver = MicronicIOMonitorDriver(host=host, port=port, timeout=timeout)
    super().__init__(driver=driver)
    self.driver: MicronicRackReaderDriver = driver
    self.default_timeout = timeout
    self.default_poll_interval = poll_interval
    self.rack_reading = RackReader(backend=MicronicRackReadingBackend(driver))
    self._capabilities = [self.rack_reading]
    if isinstance(driver, MicronicIOMonitorDriver):
      self.barcode_scanning = BarcodeScanner(
        backend=MicronicIOMonitorBarcodeScannerBackend(
          driver,
          timeout=timeout,
          poll_interval=poll_interval,
        )
      )
      self._capabilities.append(self.barcode_scanning)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "timeout": self.default_timeout,
      "poll_interval": self.default_poll_interval,
    }


class MicronicDirectCodeReader(MicronicCodeReader):
  """Micronic rack reader that controls scanner hardware directly.

  This frontend follows the same v1b1 rack-reading capability surface as
  ``MicronicCodeReader`` but uses the direct hardware driver instead of the
  Micronic IO Monitor HTTP server.
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
    min_wells: int = 96,
    keep_images: bool = False,
    image_input: Optional[str] = None,
    rack_id_override: Optional[str] = None,
    driver: Optional[MicronicDirectDriver] = None,
  ):
    if driver is None:
      driver = MicronicDirectDriver(
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
        min_wells=min_wells,
        keep_images=keep_images,
        image_input=image_input,
        rack_id_override=rack_id_override,
      )
    super().__init__(timeout=timeout, poll_interval=poll_interval, driver=driver)
    self.driver: MicronicDirectDriver = driver

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "timeout": self.default_timeout,
      "poll_interval": self.default_poll_interval,
    }
