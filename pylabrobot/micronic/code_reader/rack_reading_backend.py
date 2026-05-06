"""Rack-reading backend for Micronic rack-reader drivers."""

from __future__ import annotations

from typing import Optional, Sequence

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.rack_reading import (
  LayoutInfo,
  RackReaderBackend,
  RackReaderError,
  RackReaderState,
  RackScanResult,
)

from .direct_driver import MicronicDirectDriver
from .driver import MicronicError, MicronicIOMonitorDriver, MicronicRackReaderDriver


class MicronicRackReaderError(MicronicError, RackReaderError):
  """Raised when Micronic rack-reading operations fail."""


class MicronicRackReadingBackend(RackReaderBackend):
  """Rack-reading backend that delegates to a Micronic rack-reader driver."""

  def __init__(self, driver: MicronicRackReaderDriver):
    super().__init__()
    self.driver = driver

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    await self.get_state()

  async def get_state(self) -> RackReaderState:
    try:
      return await self.driver.get_rack_reader_state()
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def trigger_rack_scan(self) -> None:
    try:
      await self.driver.trigger_rack_scan()
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    try:
      return await self.driver.scan_rack_id(timeout=timeout, poll_interval=poll_interval)
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def get_scan_result(self) -> RackScanResult:
    try:
      return await self.driver.get_scan_result()
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def get_rack_id(self) -> str:
    try:
      return await self.driver.get_rack_id()
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def get_layouts(self) -> list[LayoutInfo]:
    try:
      return await self.driver.get_layouts()
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def get_current_layout(self) -> str:
    try:
      return await self.driver.get_current_layout()
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def set_current_layout(self, layout: str) -> None:
    try:
      await self.driver.set_current_layout(layout)
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc


class MicronicIOMonitorRackReadingBackend(MicronicRackReadingBackend):
  """Rack-reading backend for the Micronic Code Reader IO Monitor server."""

  def __init__(self, driver: MicronicIOMonitorDriver):
    super().__init__(driver=driver)


class MicronicDirectRackReadingBackend(MicronicRackReadingBackend):
  """Rack-reading backend for direct Micronic hardware control."""

  def __init__(
    self,
    driver: Optional[MicronicDirectDriver] = None,
    twain_scanner_path: Optional[str] = None,
    twain_source: str = "AVA6PlusG",
    sane_device: Optional[str] = None,
    scanner_backend: str = "auto",
    scan_command: Optional[Sequence[str]] = None,
    image_extension: Optional[str] = None,
    image_dir: Optional[str] = None,
    serial_port: str = "COM4",
    rack_id_command: Optional[Sequence[str]] = None,
    scanner_timeout_ms: int = 90000,
    serial_timeout_ms: int = 2500,
    min_wells: int = 96,
    keep_images: bool = False,
    image_input: Optional[str] = None,
    rack_id_override: Optional[str] = None,
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
        scanner_timeout_ms=scanner_timeout_ms,
        serial_timeout_ms=serial_timeout_ms,
        min_wells=min_wells,
        keep_images=keep_images,
        image_input=image_input,
        rack_id_override=rack_id_override,
      )
    super().__init__(driver=driver)
