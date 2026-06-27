"""Tecan Infinite 200 PRO luminescence backend."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_reading.luminescence.backend import LuminescenceBackend
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .driver import TecanInfiniteDriver
from .protocol import (
  _integration_microseconds_to_seconds,
  _LuminescenceRunDecoder,
  format_plate_result,
)

logger = logging.getLogger(__name__)


@dataclass
class TecanInfiniteLuminescenceParams(BackendParams):
  """Tecan Infinite-specific parameters for luminescence reads.

  Args:
    flashes: Number of flashes (reads) per well. Default 25.
    dark_integration_us: Dark integration time in microseconds. Default 3,000,000.
    meas_integration_us: Measurement integration time in microseconds. Default 1,000,000.
  """

  flashes: int = 25
  dark_integration_us: int = 3_000_000
  meas_integration_us: int = 1_000_000


class TecanInfiniteLuminescenceBackend(LuminescenceBackend):
  """Translates LuminescenceBackend interface into Tecan Infinite driver commands."""

  def __init__(self, driver: TecanInfiniteDriver):
    self.driver = driver

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    if not isinstance(backend_params, TecanInfiniteLuminescenceParams):
      backend_params = TecanInfiniteLuminescenceParams()

    if focal_height < 0:
      raise ValueError("Focal height must be non-negative for luminescence scans.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self.driver.scan_visit_order(ordered_wells, serpentine=False)

    dark_integration = backend_params.dark_integration_us
    meas_integration = backend_params.meas_integration_us

    await self.driver.begin_run()
    try:
      await self._configure_luminescence(
        dark_integration, meas_integration, focal_height, flashes=backend_params.flashes
      )

      decoder = _LuminescenceRunDecoder(
        len(scan_wells),
        dark_integration_s=_integration_microseconds_to_seconds(dark_integration),
        meas_integration_s=_integration_microseconds_to_seconds(meas_integration),
      )

      await self.driver.run_scan(
        ordered_wells=ordered_wells,
        decoder=decoder,
        mode="Luminescence",
        step_loss_commands=["CHECK MTP.STEPLOSS", "CHECK LUM.STEPLOSS"],
        serpentine=False,
        scan_direction="UP",
      )

      if len(decoder.measurements) != len(scan_wells):
        raise RuntimeError("Luminescence decoder did not complete scan.")
      intensities = [measurement.intensity for measurement in decoder.measurements]
      matrix = format_plate_result(plate, scan_wells, intensities)
      return [
        LuminescenceResult(
          data=matrix,
          temperature=None,
          timestamp=time.time(),
        )
      ]
    finally:
      await self.driver.end_run()

  async def _configure_luminescence(
    self,
    dark_integration: int,
    meas_integration: int,
    focal_height: float,
    *,
    flashes: int,
  ) -> None:
    await self.driver.send_command("MODE LUM")
    await self.driver.send_command("CHECK LUM.FIBER")
    await self.driver.send_command("CHECK LUM.LID")
    await self.driver.send_command("CHECK LUM.STEPLOSS")
    await self.driver.send_command("MODE LUM")
    reads_number = max(1, flashes)
    z_position = int(round(focal_height * self.driver.counts_per_mm_z))
    await self.driver.clear_mode_settings(emission=True)
    await self.driver.send_command(f"POSITION LUM,Z={z_position}", allow_timeout=True)
    await self.driver.send_command(f"TIME 0,INTEGRATION={dark_integration}", allow_timeout=True)
    await self.driver.send_command(f"READS 0,NUMBER={reads_number}", allow_timeout=True)
    await self.driver.send_command("SCAN DIRECTION=UP", allow_timeout=True)
    await self.driver.send_command("RATIO LABELS=1", allow_timeout=True)
    await self.driver.send_command("EMISSION 1,EMPTY,0,0,0", allow_timeout=True)
    await self.driver.send_command(f"TIME 1,INTEGRATION={meas_integration}", allow_timeout=True)
    await self.driver.send_command("TIME 1,READDELAY=0", allow_timeout=True)
    await self.driver.send_command(f"READS 1,NUMBER={reads_number}", allow_timeout=True)
    await self.driver.send_command("#EMISSION ATTENUATION", allow_timeout=True)
    await self.driver.send_command("PREPARE REF", allow_timeout=True, read_response=False)
