"""Tecan Infinite 200 PRO fluorescence backend."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_reading.fluorescence.backend import FluorescenceBackend
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .driver import TecanInfiniteDriver
from .protocol import _FluorescenceRunDecoder, format_plate_result

logger = logging.getLogger(__name__)


@dataclass
class TecanInfiniteFluorescenceParams(BackendParams):
  """Tecan Infinite-specific parameters for fluorescence reads.

  Args:
    flashes: Number of flashes (reads) per well. Default 25.
    integration_us: Integration time in microseconds. Default 20.
    gain: PMT gain value (0-255). Default 100.
    excitation_bandwidth: Excitation filter bandwidth in deci-tenths of nm. Default 50.
    emission_bandwidth: Emission filter bandwidth in deci-tenths of nm. Default 200.
    lag_us: Lag time in microseconds between excitation and measurement. Default 0.
  """

  flashes: int = 25
  integration_us: int = 20
  gain: int = 100
  excitation_bandwidth: int = 50
  emission_bandwidth: int = 200
  lag_us: int = 0


class TecanInfiniteFluorescenceBackend(FluorescenceBackend):
  """Translates FluorescenceBackend interface into Tecan Infinite driver commands."""

  def __init__(self, driver: TecanInfiniteDriver):
    self.driver = driver

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[FluorescenceResult]:
    if not isinstance(backend_params, TecanInfiniteFluorescenceParams):
      backend_params = TecanInfiniteFluorescenceParams()

    if not 230 <= excitation_wavelength <= 850:
      raise ValueError("Excitation wavelength must be between 230 nm and 850 nm.")
    if not 230 <= emission_wavelength <= 850:
      raise ValueError("Emission wavelength must be between 230 nm and 850 nm.")
    if focal_height < 0:
      raise ValueError("Focal height must be non-negative for fluorescence scans.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self.driver.scan_visit_order(ordered_wells, serpentine=True)

    await self.driver.begin_run()
    try:
      await self._configure_fluorescence(
        excitation_wavelength,
        emission_wavelength,
        focal_height,
        flashes=backend_params.flashes,
        integration_us=backend_params.integration_us,
        gain=backend_params.gain,
        excitation_bandwidth=backend_params.excitation_bandwidth,
        emission_bandwidth=backend_params.emission_bandwidth,
        lag_us=backend_params.lag_us,
      )
      decoder = _FluorescenceRunDecoder(len(scan_wells))

      await self.driver.run_scan(
        ordered_wells=ordered_wells,
        decoder=decoder,
        mode="Fluorescence",
        step_loss_commands=[
          "CHECK MTP.STEPLOSS",
          "CHECK FI.TOP.STEPLOSS",
          "CHECK FI.STEPLOSS.Z",
        ],
        serpentine=True,
        scan_direction="UP",
      )

      if len(decoder.intensities) != len(scan_wells):
        raise RuntimeError("Fluorescence decoder did not complete scan.")
      intensities = decoder.intensities
      matrix = format_plate_result(plate, scan_wells, intensities)
      return [
        FluorescenceResult(
          data=matrix,
          excitation_wavelength=excitation_wavelength,
          emission_wavelength=emission_wavelength,
          temperature=None,
          timestamp=time.time(),
        )
      ]
    finally:
      await self.driver.end_run()

  async def _configure_fluorescence(
    self,
    excitation_nm: int,
    emission_nm: int,
    focal_height: float,
    *,
    flashes: int,
    integration_us: int,
    gain: int,
    excitation_bandwidth: int,
    emission_bandwidth: int,
    lag_us: int,
  ) -> None:
    ex_decitenth = int(round(excitation_nm * 10))
    em_decitenth = int(round(emission_nm * 10))
    reads_number = max(1, flashes)
    beam_diameter = self.driver.capability_numeric("FI.TOP", "#BEAM DIAMETER", 3000)
    z_position = int(round(focal_height * self.driver.counts_per_mm_z))

    # UI issues the entire FI configuration twice before PREPARE REF.
    for _ in range(2):
      await self.driver.send_command("MODE FI.TOP", allow_timeout=True)
      await self.driver.clear_mode_settings(excitation=True, emission=True)
      await self.driver.send_command(
        f"EXCITATION 0,FI,{ex_decitenth},{excitation_bandwidth},0", allow_timeout=True
      )
      await self.driver.send_command(
        f"EMISSION 0,FI,{em_decitenth},{emission_bandwidth},0", allow_timeout=True
      )
      await self.driver.send_command(f"TIME 0,INTEGRATION={integration_us}", allow_timeout=True)
      await self.driver.send_command(f"TIME 0,LAG={lag_us}", allow_timeout=True)
      await self.driver.send_command("TIME 0,READDELAY=0", allow_timeout=True)
      await self.driver.send_command(f"GAIN 0,VALUE={gain}", allow_timeout=True)
      await self.driver.send_command(f"POSITION 0,Z={z_position}", allow_timeout=True)
      await self.driver.send_command(f"BEAM DIAMETER={beam_diameter}", allow_timeout=True)
      await self.driver.send_command("SCAN DIRECTION=UP", allow_timeout=True)
      await self.driver.send_command("RATIO LABELS=1", allow_timeout=True)
      await self.driver.send_command(f"READS 0,NUMBER={reads_number}", allow_timeout=True)
      await self.driver.send_command(
        f"EXCITATION 1,FI,{ex_decitenth},{excitation_bandwidth},0", allow_timeout=True
      )
      await self.driver.send_command(
        f"EMISSION 1,FI,{em_decitenth},{emission_bandwidth},0", allow_timeout=True
      )
      await self.driver.send_command(f"TIME 1,INTEGRATION={integration_us}", allow_timeout=True)
      await self.driver.send_command(f"TIME 1,LAG={lag_us}", allow_timeout=True)
      await self.driver.send_command("TIME 1,READDELAY=0", allow_timeout=True)
      await self.driver.send_command(f"GAIN 1,VALUE={gain}", allow_timeout=True)
      await self.driver.send_command(f"POSITION 1,Z={z_position}", allow_timeout=True)
      await self.driver.send_command(f"READS 1,NUMBER={reads_number}", allow_timeout=True)
    await self.driver.send_command("PREPARE REF", allow_timeout=True, read_response=False)
