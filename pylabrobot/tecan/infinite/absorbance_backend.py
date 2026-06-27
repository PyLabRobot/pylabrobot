"""Tecan Infinite 200 PRO absorbance backend."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_reading.absorbance.backend import AbsorbanceBackend
from pylabrobot.capabilities.plate_reading.absorbance.standard import AbsorbanceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .driver import TecanInfiniteDriver
from .protocol import _absorbance_od_calibrated, _AbsorbanceRunDecoder, format_plate_result

logger = logging.getLogger(__name__)


@dataclass
class TecanInfiniteAbsorbanceParams(BackendParams):
  """Tecan Infinite-specific parameters for absorbance reads.

  Args:
    flashes: Number of flashes (reads) per well. Default 25.
    bandwidth: Excitation bandwidth in nm. If None, auto-selected
      (9 nm for >315 nm, 5 nm otherwise).
  """

  flashes: int = 25
  bandwidth: Optional[float] = None


class TecanInfiniteAbsorbanceBackend(AbsorbanceBackend):
  """Translates AbsorbanceBackend interface into Tecan Infinite driver commands."""

  def __init__(self, driver: TecanInfiniteDriver):
    self.driver = driver

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[AbsorbanceResult]:
    if not isinstance(backend_params, TecanInfiniteAbsorbanceParams):
      backend_params = TecanInfiniteAbsorbanceParams()

    if not 230 <= wavelength <= 1_000:
      raise ValueError("Absorbance wavelength must be between 230 nm and 1000 nm.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self.driver.scan_visit_order(ordered_wells, serpentine=True)
    decoder = _AbsorbanceRunDecoder(len(scan_wells))

    await self.driver.begin_run()
    try:
      await self._configure_absorbance(
        wavelength, flashes=backend_params.flashes, bandwidth=backend_params.bandwidth
      )
      await self.driver.run_scan(
        ordered_wells=ordered_wells,
        decoder=decoder,
        mode="Absorbance",
        step_loss_commands=["CHECK MTP.STEPLOSS", "CHECK ABS.STEPLOSS"],
        serpentine=True,
        scan_direction="ALTUP",
      )

      self.driver.drain_pending_bin_events(decoder)
      if len(decoder.measurements) != len(scan_wells):
        raise RuntimeError("Absorbance decoder did not complete scan.")
      intensities: List[float] = []
      cal = decoder.calibration
      if cal is None:
        raise RuntimeError("ABS calibration packet not seen; cannot compute calibrated OD.")
      for meas in decoder.measurements:
        items = meas.items or [(meas.sample, meas.reference)]
        od = _absorbance_od_calibrated(cal, items)
        intensities.append(od)
      matrix = format_plate_result(plate, scan_wells, intensities)
      return [
        AbsorbanceResult(
          data=matrix,
          wavelength=wavelength,
          temperature=None,
          timestamp=time.time(),
        )
      ]
    finally:
      await self.driver.end_run()

  async def _configure_absorbance(
    self,
    wavelength_nm: int,
    *,
    flashes: int,
    bandwidth: Optional[float] = None,
  ) -> None:
    wl_decitenth = int(round(wavelength_nm * 10))
    bw = bandwidth if bandwidth is not None else self._auto_bandwidth(wavelength_nm)
    bw_decitenth = int(round(bw * 10))
    reads_number = max(1, flashes)

    await self.driver.send_command("MODE ABS")
    await self.driver.clear_mode_settings(excitation=True)
    await self.driver.send_command(
      f"EXCITATION 0,ABS,{wl_decitenth},{bw_decitenth},0", allow_timeout=True
    )
    await self.driver.send_command(
      f"EXCITATION 1,ABS,{wl_decitenth},{bw_decitenth},0", allow_timeout=True
    )
    await self.driver.send_command(f"READS 0,NUMBER={reads_number}", allow_timeout=True)
    await self.driver.send_command(f"READS 1,NUMBER={reads_number}", allow_timeout=True)
    await self.driver.send_command("TIME 0,READDELAY=0", allow_timeout=True)
    await self.driver.send_command("TIME 1,READDELAY=0", allow_timeout=True)
    await self.driver.send_command("SCAN DIRECTION=ALTUP", allow_timeout=True)
    await self.driver.send_command("#RATIO LABELS", allow_timeout=True)
    await self.driver.send_command(
      f"BEAM DIAMETER={self.driver.capability_numeric('ABS', '#BEAM DIAMETER', 700)}",
      allow_timeout=True,
    )
    await self.driver.send_command("RATIO LABELS=1", allow_timeout=True)
    await self.driver.send_command("PREPARE REF", allow_timeout=True, read_response=False)

  @staticmethod
  def _auto_bandwidth(wavelength_nm: int) -> float:
    return 9.0 if wavelength_nm > 315 else 5.0
