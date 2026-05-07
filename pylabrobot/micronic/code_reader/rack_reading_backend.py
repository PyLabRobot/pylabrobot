"""Rack-reading backend for the Micronic driver."""

from __future__ import annotations

from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.rack_reading import (
  LayoutInfo,
  RackReaderBackend,
  RackReaderError,
  RackReaderState,
  RackScanResult,
)

from .driver import MicronicDriver, MicronicError


class MicronicRackReaderError(MicronicError, RackReaderError):
  """Raised when Micronic rack-reading operations fail."""


class MicronicRackReadingBackend(RackReaderBackend):
  """Rack-reading backend that delegates to the Micronic driver."""

  def __init__(self, driver: MicronicDriver):
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
