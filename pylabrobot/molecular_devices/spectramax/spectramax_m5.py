from typing import Dict, List, Optional, Union

from pylabrobot.capabilities.plate_reading.absorbance import AbsorbanceCapability
from pylabrobot.capabilities.plate_reading.fluorescence import FluorescenceCapability
from pylabrobot.capabilities.plate_reading.fluorescence.backend import FluorescenceBackend
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.capabilities.plate_reading.luminescence import LuminescenceCapability
from pylabrobot.capabilities.plate_reading.luminescence.backend import LuminescenceBackend
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

from .backend import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesBackend,
  MolecularDevicesSettings,
  PmtGain,
  ReadMode,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)


class SpectraMaxM5Backend(MolecularDevicesBackend, FluorescenceBackend, LuminescenceBackend):
  """Backend for Molecular Devices SpectraMax M5 plate readers.

  Supports absorbance (inherited), fluorescence, luminescence, fluorescence polarization,
  and time-resolved fluorescence.
  """

  def __init__(self, port: str) -> None:
    super().__init__(port, human_readable_device_name="Molecular Devices SpectraMax M5")

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    *,
    excitation_wavelengths: Optional[List[int]] = None,
    emission_wavelengths: Optional[List[int]] = None,
    cutoff_filters: Optional[List[int]] = None,
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 10,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[FluorescenceResult]:
    if excitation_wavelengths is None:
      excitation_wavelengths = [excitation_wavelength]
    if emission_wavelengths is None:
      emission_wavelengths = [emission_wavelength]
    if cutoff_filters is None:
      cutoff_filters = [self._get_cutoff_filter_index_from_wavelength(emission_wavelength)]

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.FLU,
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
    await self._wait_for_idle(timeout=timeout)
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

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    *,
    emission_wavelengths: Optional[List[int]] = None,
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 0,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[LuminescenceResult]:
    if emission_wavelengths is None:
      raise ValueError("emission_wavelengths is required for SpectraMax M5 luminescence reads")

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.LUM,
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
    await self._wait_for_idle(timeout=timeout)
    dicts = await self._transfer_data(settings)
    return [
      LuminescenceResult(
        data=d["data"],
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
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 10,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.POLAR,
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
    await self._wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)

  async def read_time_resolved_fluorescence(
    self,
    plate: Plate,
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    delay_time: int,
    integration_time: int,
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 50,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.TIME,
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
    await self._wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class SpectraMaxM5(Resource, Device):
  """Molecular Devices SpectraMax M5 plate reader.

  Supports absorbance, fluorescence, and luminescence capabilities.
  Also supports fluorescence polarization and time-resolved fluorescence
  via direct backend access.
  """

  def __init__(
    self,
    name: str,
    port: str,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    backend = SpectraMaxM5Backend(port=port)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Molecular Devices SpectraMax M5",
    )
    Device.__init__(self, backend=backend)
    self._backend: SpectraMaxM5Backend = backend
    self.absorbance = AbsorbanceCapability(backend=backend)
    self.luminescence = LuminescenceCapability(backend=backend)
    self.fluorescence = FluorescenceCapability(backend=backend)
    self.tc = TemperatureControlCapability(backend=backend)
    self._capabilities = [self.absorbance, self.luminescence, self.fluorescence, self.tc]

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,  # TODO: measure
      pedestal_size_z=0,  # TODO: measure
      child_location=Coordinate.zero(),  # TODO: measure
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self) -> None:
    await self._backend.open()

  async def close(self) -> None:
    await self._backend.close()
