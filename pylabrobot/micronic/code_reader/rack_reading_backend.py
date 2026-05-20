"""Rack-reading backend for the Micronic driver."""

from __future__ import annotations

import asyncio
import logging

from pylabrobot.capabilities.rack_reading import RackReaderBackend, RackScanEntry, RackScanResult
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.tube_rack import TubeRack

from .driver import RACK_COLS, RACK_ROWS, MicronicCodeReaderDriver, decode_image, iter_positions
from .errors import MicronicError

logger = logging.getLogger(__name__)


class MicronicCodeReaderRackReadingBackend(RackReaderBackend):
  """Rack-reading backend for the Micronic code reader."""

  def __init__(self, driver: MicronicCodeReaderDriver):
    super().__init__()
    self.driver = driver
    self._scan_lock = asyncio.Lock()

  @staticmethod
  def _validate_rack(rack: TubeRack) -> None:
    if rack.num_items_x != RACK_COLS or rack.num_items_y != RACK_ROWS:
      raise MicronicError(
        f"Micronic code reader only supports {RACK_ROWS}x{RACK_COLS} racks; "
        f"got {rack.num_items_y}x{rack.num_items_x}."
      )

  async def scan_rack(self, rack: TubeRack, timeout: float, poll_interval: float) -> RackScanResult:
    del poll_interval
    return await asyncio.wait_for(self._scan_rack(rack), timeout=timeout)

  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    del poll_interval
    return await asyncio.wait_for(self.driver.read_barcode(), timeout=timeout)

  async def _scan_rack(self, rack: TubeRack) -> RackScanResult:
    self._validate_rack(rack)
    if self._scan_lock.locked():
      raise MicronicError("Micronic rack scan is already in progress.")
    async with self._scan_lock:
      rack_id = await self.driver.read_barcode()
      loop = asyncio.get_running_loop()
      return await loop.run_in_executor(None, self._scan_rack_blocking, rack_id, rack.num_items)

  def _scan_rack_blocking(self, rack_id: str, expected_well_count: int) -> RackScanResult:
    image_path = self.driver.acquire_image()

    try:
      decoded, self.driver.last_decode_metadata = decode_image(image_path)
      if len(decoded) < expected_well_count:
        missing = ", ".join(position for position in iter_positions() if position not in decoded)
        raise MicronicError(
          f"Micronic decode found {len(decoded)} wells; expected at least "
          f"{expected_well_count}. Missing: {missing}"
        )

      for position, result in decoded.items():
        logger.debug("Micronic decoded %s via %s", position, result.method)

      entries = [
        RackScanEntry(
          position=position,
          tube_id=decoded[position].tube_id if position in decoded else None,
          status="OK" if position in decoded else "NOREAD",
          barcode=(
            Barcode(
              data=decoded[position].tube_id,
              symbology="DataMatrix",
              position_on_resource="bottom",
            )
            if position in decoded
            else None
          ),
        )
        for position in iter_positions()
      ]

      return RackScanResult(
        rack_id=rack_id,
        entries=entries,
        rack_barcode=Barcode(
          data=rack_id,
          symbology="Code 128 (Subset B and C)",
          position_on_resource="right",
        )
        if rack_id != "NOREAD"
        else None,
      )
    finally:
      self.driver.release_image(image_path)
