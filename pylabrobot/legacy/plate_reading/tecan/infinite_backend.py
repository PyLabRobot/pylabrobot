"""Tecan Infinite 200 PRO backend.

Legacy wrapper. Use :class:`pylabrobot.tecan.infinite.TecanInfinite200Pro` instead.

This module delegates to the new Device/Driver/CapabilityBackend architecture
while preserving the legacy ``PlateReaderBackend`` API and all internal symbols
imported by existing tests.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from pylabrobot.io.usb import USB  # noqa: F401 — test patches this import location
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate
from pylabrobot.resources.well import Well
from pylabrobot.tecan.infinite.absorbance_backend import (
  TecanInfiniteAbsorbanceBackend,
  TecanInfiniteAbsorbanceParams,
)
from pylabrobot.tecan.infinite.driver import TecanInfiniteDriver
from pylabrobot.tecan.infinite.fluorescence_backend import (
  TecanInfiniteFluorescenceBackend,
  TecanInfiniteFluorescenceParams,
)
from pylabrobot.tecan.infinite.luminescence_backend import (
  TecanInfiniteLuminescenceBackend,
  TecanInfiniteLuminescenceParams,
)

# Re-export protocol symbols so existing test imports continue to work.
from pylabrobot.tecan.infinite.protocol import (  # noqa: F401
  BIN_RE,
  StagePosition,
  _absorbance_od_calibrated,
  _AbsorbanceCalibration,
  _AbsorbanceCalibrationItem,
  _AbsorbanceMeasurement,
  _AbsorbanceRunDecoder,
  _consume_leading_ascii_frame,
  _consume_status_frame,
  _decode_abs_calibration,
  _decode_abs_data,
  _decode_flr_calibration,
  _decode_flr_data,
  _decode_lum_calibration,
  _decode_lum_data,
  _fluorescence_corrected,
  _FluorescenceCalibration,
  _FluorescenceRunDecoder,
  _integration_microseconds_to_seconds,
  _is_abs_calibration_len,
  _is_abs_data_len,
  _luminescence_intensity,
  _LuminescenceCalibration,
  _LuminescenceMeasurement,
  _LuminescenceRunDecoder,
  _MeasurementDecoder,
  _split_payload_and_trailer,
  _StreamEvent,
  _StreamParser,
  format_plate_result,
  frame_command,
  is_terminal_frame,
)

logger = logging.getLogger(__name__)


class ExperimentalTecanInfinite200ProBackend(PlateReaderBackend):
  """Legacy wrapper around the new Tecan Infinite architecture.

  Use :class:`pylabrobot.tecan.infinite.TecanInfinite200Pro` for new code.
  """

  VENDOR_ID = TecanInfiniteDriver.VENDOR_ID
  PRODUCT_ID = TecanInfiniteDriver.PRODUCT_ID

  _MODE_CAPABILITY_COMMANDS = TecanInfiniteDriver._MODE_CAPABILITY_COMMANDS

  def __init__(
    self,
    counts_per_mm_x: float = 1_000,
    counts_per_mm_y: float = 1_000,
    counts_per_mm_z: float = 1_000,
  ) -> None:
    super().__init__()
    # Create USB here so that test patches on
    # "pylabrobot.legacy.plate_reading.tecan.infinite_backend.USB"
    # are picked up. Pass the io instance to the driver.
    io = USB(
      id_vendor=self.VENDOR_ID,
      id_product=self.PRODUCT_ID,
      human_readable_device_name="Tecan Infinite 200 PRO",
      packet_read_timeout=3,
      read_timeout=30,
    )
    self._driver = TecanInfiniteDriver(
      counts_per_mm_x=counts_per_mm_x,
      counts_per_mm_y=counts_per_mm_y,
      counts_per_mm_z=counts_per_mm_z,
      io=io,
    )
    self._absorbance = TecanInfiniteAbsorbanceBackend(self._driver)
    self._fluorescence = TecanInfiniteFluorescenceBackend(self._driver)
    self._luminescence = TecanInfiniteLuminescenceBackend(self._driver)

    # Alias for direct attribute access (legacy code)
    self.io = io
    self.counts_per_mm_x = counts_per_mm_x
    self.counts_per_mm_y = counts_per_mm_y
    self.counts_per_mm_z = counts_per_mm_z

  # -- state proxies for test compat --

  @property
  def _ready(self):
    return self._driver._ready

  @_ready.setter
  def _ready(self, value):
    self._driver._ready = value

  @property
  def _pending_bin_events(self):
    return self._driver._pending_bin_events

  @_pending_bin_events.setter
  def _pending_bin_events(self, value):
    self._driver._pending_bin_events = value

  @property
  def _mode_capabilities(self):
    return self._driver._mode_capabilities

  @property
  def _parser(self):
    return self._driver._parser

  @property
  def _run_active(self):
    return self._driver._run_active

  @property
  def _active_step_loss_commands(self):
    return self._driver._active_step_loss_commands

  @property
  def _read_chunk_size(self):
    return self._driver._read_chunk_size

  @property
  def _max_row_wait_s(self):
    return self._driver._max_row_wait_s

  # -- lifecycle --

  async def setup(self) -> None:
    await self._driver.setup()

  async def stop(self) -> None:
    await self._driver.stop()

  # -- tray --

  async def open(self) -> None:
    await self._driver.open_tray()

  async def close(self, plate: Optional[Plate] = None) -> None:  # noqa: ARG002
    await self._driver.close_tray()

  # -- reads: delegate to backends, convert Result -> dict --

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    flashes: int = 25,
    bandwidth: Optional[float] = None,
  ) -> List[Dict]:
    params = TecanInfiniteAbsorbanceParams(flashes=flashes, bandwidth=bandwidth)
    results = await self._absorbance.read_absorbance(
      plate=plate,
      wells=wells,
      wavelength=wavelength,
      backend_params=params,
    )
    return [
      {
        "wavelength": r.wavelength,
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float = 20.0,
    flashes: int = 25,
    integration_us: int = 20,
    gain: int = 100,
    excitation_bandwidth: int = 50,
    emission_bandwidth: int = 200,
    lag_us: int = 0,
  ) -> List[Dict]:
    params = TecanInfiniteFluorescenceParams(
      flashes=flashes,
      integration_us=integration_us,
      gain=gain,
      excitation_bandwidth=excitation_bandwidth,
      emission_bandwidth=emission_bandwidth,
      lag_us=lag_us,
    )
    results = await self._fluorescence.read_fluorescence(
      plate=plate,
      wells=wells,
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
      backend_params=params,
    )
    return [
      {
        "ex_wavelength": r.excitation_wavelength,
        "em_wavelength": r.emission_wavelength,
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float = 20.0,
    flashes: int = 25,
    dark_integration_us: int = 3_000_000,
    meas_integration_us: int = 1_000_000,
  ) -> List[Dict]:
    params = TecanInfiniteLuminescenceParams(
      flashes=flashes,
      dark_integration_us=dark_integration_us,
      meas_integration_us=meas_integration_us,
    )
    results = await self._luminescence.read_luminescence(
      plate=plate,
      wells=wells,
      focal_height=focal_height,
      backend_params=params,
    )
    return [
      {
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

  # -- method delegates for test compat --

  @staticmethod
  def _frame_command(command: str) -> bytes:
    return frame_command(command)

  @staticmethod
  def _is_terminal_frame(text: str) -> bool:
    return is_terminal_frame(text)

  def _scan_visit_order(self, wells, serpentine=True):
    return self._driver.scan_visit_order(wells, serpentine)

  def _group_by_row(self, wells):
    return self._driver.group_by_row(wells)

  def _scan_range(self, row_index, row_wells, serpentine=True):
    return self._driver.scan_range(row_index, row_wells, serpentine)

  def _map_well_to_stage(self, well):
    return self._driver.map_well_to_stage(well)

  def _format_plate_result(self, plate, scan_wells, values):
    return format_plate_result(plate, scan_wells, values)

  def _capability_numeric(self, mode, command, fallback):
    return self._driver.capability_numeric(mode, command, fallback)

  async def _send_command(self, command, **kwargs):
    return await self._driver.send_command(command, **kwargs)


__all__ = [
  "ExperimentalTecanInfinite200ProBackend",
]
