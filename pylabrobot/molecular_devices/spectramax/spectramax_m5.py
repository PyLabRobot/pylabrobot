import logging
from typing import Dict, List, Optional, Union

from pylabrobot.molecular_devices.spectramax.results import (
  FluorescenceResult,
  LuminescenceResult,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

from .base import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesPlateReader,
  MolecularDevicesSettings,
  PmtGain,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)

logger = logging.getLogger(__name__)


class SpectraMaxM5(MolecularDevicesPlateReader):
  def __init__(
    self,
    name: str,
    port: str,
  ):
    super().__init__(port=port, human_readable_device_name="Molecular Devices SpectraMax M5")
    self.loading_tray = PlateHolder(
      name=name + "_loading_tray",
      size_x=127.76,
      size_y=85.48,
      size_z=0,  # TODO: measure
      pedestal_size_z=0,  # TODO: measure
      child_location=Coordinate.zero(),  # TODO: measure
    )

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    excitation_wavelengths: Optional[List[int]] = None,
    emission_wavelengths: Optional[List[int]] = None,
    cutoff_filters: Optional[List[int]] = None,
    read_type: ReadType = "endpoint",
    read_order: ReadOrder = "column",
    calibrate: Calibrate = "once",
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = "normal",
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = "auto",
    flashes_per_well: int = 10,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[FluorescenceResult]:
    """Read fluorescence.

    Args:
      plate: Plate to read.
      wells: Wells to read.
      excitation_wavelength: Excitation wavelength in nm, used when
        ``excitation_wavelengths`` is not given.
      emission_wavelength: Emission wavelength in nm, used when ``emission_wavelengths``
        is not given.
      focal_height: Focal height in mm.
      excitation_wavelengths: List of excitation wavelengths in nm.
      emission_wavelengths: List of emission wavelengths in nm.
      cutoff_filters: List of cutoff filter indices. If None, auto-selected from the
        emission wavelength.
      read_type: Read type (endpoint, kinetic, spectrum, or well scan).
      read_order: Read order (column or wavelength).
      calibrate: Calibration mode (on, once, or off).
      shake_settings: Optional shake settings to apply before reading.
      carriage_speed: Carriage speed (normal or slow).
      read_from_bottom: If True, read from the bottom of the plate.
      pmt_gain: PMT gain setting (a named setting or a manual integer value).
      flashes_per_well: Number of flashes per well.
      kinetic_settings: Optional kinetic read settings (for kinetic read type).
      spectrum_settings: Optional spectrum scan settings (for spectrum read type).
      cuvette: If True, read a cuvette instead of a plate.
      settling_time: Settling time in milliseconds before reading.
      timeout: Read timeout in seconds.
    """

    logger.info(
      "[SpectraMax M5 %s] read fluorescence: plate=%s, ex=%d nm, em=%d nm, wells=%d",
      self.port,
      plate.name,
      excitation_wavelength,
      emission_wavelength,
      len(wells),
    )
    excitation_wavelengths = excitation_wavelengths or [excitation_wavelength]
    emission_wavelengths = emission_wavelengths or [emission_wavelength]
    cutoff_filters = cutoff_filters
    if cutoff_filters is None:
      cutoff_filters = [self._get_cutoff_filter_index_from_wavelength(emission_wavelength)]

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode="fluorescence",
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
    )
    await self._set_clear()
    if not cuvette:
      await self._set_plate_position(settings)
      await self._set_strip(settings)
      await self._set_carriage_speed(settings)

    await self._set_shake(settings)
    await self._set_flashes_per_well(settings)
    await self._set_pmt(settings)
    await self._set_wavelengths(settings)
    await self._set_filter(settings)
    await self._set_read_stage(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)
    await self._set_tag(settings)
    await self._set_nvram(settings)
    await self._set_readtype(settings)

    await self._read_now()
    await self.wait_for_idle(timeout=timeout)
    dicts = await self._transfer_data(settings)
    return [
      FluorescenceResult(
        data=d["data"],
        excitation_wavelength=d["ex_wavelength"],
        emission_wavelength=d["em_wavelength"],
        temperature=d["temperature"],
        timestamp=d["time"],
      )
      for d in dicts
    ]

  async def read_fluorescence_polarization(
    self,
    plate: Plate,
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    read_type: ReadType = "endpoint",
    read_order: ReadOrder = "column",
    calibrate: Calibrate = "once",
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = "normal",
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = "auto",
    flashes_per_well: int = 10,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    """Read fluorescence polarization."""
    logger.info(
      "[SpectraMax M5 %s] read fluorescence polarization: plate='%s', ex=%s nm, em=%s nm",
      self.port,
      plate.name,
      excitation_wavelengths,
      emission_wavelengths,
    )
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode="polarization",
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
    )
    await self._set_clear()
    if not cuvette:
      await self._set_plate_position(settings)
      await self._set_strip(settings)
      await self._set_carriage_speed(settings)

    await self._set_shake(settings)
    await self._set_flashes_per_well(settings)
    await self._set_pmt(settings)
    await self._set_wavelengths(settings)
    await self._set_filter(settings)
    await self._set_read_stage(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)
    await self._set_tag(settings)
    await self._set_nvram(settings)
    await self._set_readtype(settings)

    await self._read_now()
    await self.wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)

  async def read_time_resolved_fluorescence(
    self,
    plate: Plate,
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    delay_time: int,
    integration_time: int,
    read_type: ReadType = "endpoint",
    read_order: ReadOrder = "column",
    calibrate: Calibrate = "once",
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = "normal",
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = "auto",
    flashes_per_well: int = 50,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    """Read time-resolved fluorescence."""
    logger.info(
      "[SpectraMax M5 %s] read time-resolved fluorescence: plate='%s', ex=%s nm, em=%s nm",
      self.port,
      plate.name,
      excitation_wavelengths,
      emission_wavelengths,
    )
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode="time_resolved",
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
    )
    await self._set_clear()
    await self._set_readtype(settings)
    await self._set_integration_time(settings, delay_time, integration_time)

    if not cuvette:
      await self._set_plate_position(settings)
      await self._set_strip(settings)
      await self._set_carriage_speed(settings)

    await self._set_shake(settings)
    await self._set_flashes_per_well(settings)
    await self._set_pmt(settings)
    await self._set_wavelengths(settings)
    await self._set_filter(settings)
    await self._set_calibrate(settings)
    await self._set_read_stage(settings)
    await self._set_mode(settings)
    await self._set_order(settings)
    await self._set_tag(settings)
    await self._set_nvram(settings)

    await self._read_now()
    await self.wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    emission_wavelengths: Optional[List[int]] = None,
    read_type: ReadType = "endpoint",
    read_order: ReadOrder = "column",
    calibrate: Calibrate = "once",
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = "normal",
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = "auto",
    flashes_per_well: int = 0,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[LuminescenceResult]:
    """Read luminescence.

    Args:
      plate: Plate to read.
      wells: Wells to read.
      focal_height: Focal height in mm.
      emission_wavelengths: List of emission wavelengths in nm. Required for
        SpectraMax M5 luminescence reads.
      read_type: Read type (endpoint, kinetic, spectrum, or well scan).
      read_order: Read order (column or wavelength).
      calibrate: Calibration mode (on, once, or off).
      shake_settings: Optional shake settings to apply before reading.
      carriage_speed: Carriage speed (normal or slow).
      read_from_bottom: If True, read from the bottom of the plate.
      pmt_gain: PMT gain setting (a named setting or a manual integer value).
      flashes_per_well: Number of flashes per well. 0 uses the instrument default.
      kinetic_settings: Optional kinetic read settings (for kinetic read type).
      spectrum_settings: Optional spectrum scan settings (for spectrum read type).
      cuvette: If True, read a cuvette instead of a plate.
      settling_time: Settling time in milliseconds before reading.
      timeout: Read timeout in seconds.
    """

    logger.info(
      "[SpectraMax M5 %s] read luminescence: plate=%s, wells=%d",
      self.port,
      plate.name,
      len(wells),
    )
    if emission_wavelengths is None:
      raise ValueError("emission_wavelengths is required for SpectraMax M5 luminescence reads")

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode="luminescence",
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      emission_wavelengths=emission_wavelengths,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
    )
    await self._set_clear()
    await self._set_read_stage(settings)

    if not cuvette:
      await self._set_plate_position(settings)
      await self._set_strip(settings)
      await self._set_carriage_speed(settings)

    await self._set_shake(settings)
    await self._set_pmt(settings)
    await self._set_wavelengths(settings)
    await self._set_read_stage(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)
    await self._set_tag(settings)
    await self._set_nvram(settings)
    await self._set_readtype(settings)

    await self._read_now()
    await self.wait_for_idle(timeout=timeout)
    dicts = await self._transfer_data(settings)
    return [
      LuminescenceResult(
        data=d["data"],
        temperature=d["temperature"],
        timestamp=d["time"],
      )
      for d in dicts
    ]
