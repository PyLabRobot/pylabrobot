from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.fluorescence.backend import FluorescenceBackend
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence
from pylabrobot.capabilities.plate_reading.luminescence.backend import LuminescenceBackend
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .backend import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesAbsorbanceBackend,
  MolecularDevicesDriver,
  MolecularDevicesSettings,
  MolecularDevicesTemperatureBackend,
  PmtGain,
  ReadMode,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
  _MolecularDevicesProtocol,
)


class SpectraMaxM5FluorescenceBackend(_MolecularDevicesProtocol, FluorescenceBackend):
  """Translates FluorescenceBackend interface into SpectraMax M5 commands."""

  def __init__(self, driver: MolecularDevicesDriver) -> None:
    self._driver = driver

  @dataclass
  class FluorescenceParams(BackendParams):
    excitation_wavelengths: Optional[List[int]] = None
    emission_wavelengths: Optional[List[int]] = None
    cutoff_filters: Optional[List[int]] = None
    read_type: ReadType = ReadType.ENDPOINT
    read_order: ReadOrder = ReadOrder.COLUMN
    calibrate: Calibrate = Calibrate.ONCE
    shake_settings: Optional[ShakeSettings] = None
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL
    read_from_bottom: bool = False
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO
    flashes_per_well: int = 10
    kinetic_settings: Optional[KineticSettings] = None
    spectrum_settings: Optional[SpectrumSettings] = None
    cuvette: bool = False
    settling_time: int = 0
    timeout: int = 600

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[FluorescenceResult]:
    if not isinstance(backend_params, self.FluorescenceParams):
      backend_params = SpectraMaxM5FluorescenceBackend.FluorescenceParams()

    excitation_wavelengths = backend_params.excitation_wavelengths or [excitation_wavelength]
    emission_wavelengths = backend_params.emission_wavelengths or [emission_wavelength]
    cutoff_filters = backend_params.cutoff_filters
    if cutoff_filters is None:
      cutoff_filters = [self._get_cutoff_filter_index_from_wavelength(emission_wavelength)]

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.FLU,
      read_type=backend_params.read_type,
      read_order=backend_params.read_order,
      calibrate=backend_params.calibrate,
      shake_settings=backend_params.shake_settings,
      carriage_speed=backend_params.carriage_speed,
      read_from_bottom=backend_params.read_from_bottom,
      pmt_gain=backend_params.pmt_gain,
      flashes_per_well=backend_params.flashes_per_well,
      kinetic_settings=backend_params.kinetic_settings,
      spectrum_settings=backend_params.spectrum_settings,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=backend_params.cuvette,
      speed_read=False,
      settling_time=backend_params.settling_time,
    )
    await self._set_clear()
    if not backend_params.cuvette:
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
    await self._driver.wait_for_idle(timeout=backend_params.timeout)
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
    """Read fluorescence polarization."""
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
    await self._driver.wait_for_idle(timeout=timeout)
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
    """Read time-resolved fluorescence."""
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
    await self._driver.wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)


class SpectraMaxM5LuminescenceBackend(_MolecularDevicesProtocol, LuminescenceBackend):
  """Translates LuminescenceBackend interface into SpectraMax M5 commands."""

  def __init__(self, driver: MolecularDevicesDriver) -> None:
    self._driver = driver

  @dataclass
  class LuminescenceParams(BackendParams):
    emission_wavelengths: Optional[List[int]] = None
    read_type: ReadType = ReadType.ENDPOINT
    read_order: ReadOrder = ReadOrder.COLUMN
    calibrate: Calibrate = Calibrate.ONCE
    shake_settings: Optional[ShakeSettings] = None
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL
    read_from_bottom: bool = False
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO
    flashes_per_well: int = 0
    kinetic_settings: Optional[KineticSettings] = None
    spectrum_settings: Optional[SpectrumSettings] = None
    cuvette: bool = False
    settling_time: int = 0
    timeout: int = 600

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    if not isinstance(backend_params, self.LuminescenceParams):
      backend_params = SpectraMaxM5LuminescenceBackend.LuminescenceParams()

    if backend_params.emission_wavelengths is None:
      raise ValueError("emission_wavelengths is required for SpectraMax M5 luminescence reads")

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.LUM,
      read_type=backend_params.read_type,
      read_order=backend_params.read_order,
      calibrate=backend_params.calibrate,
      shake_settings=backend_params.shake_settings,
      carriage_speed=backend_params.carriage_speed,
      read_from_bottom=backend_params.read_from_bottom,
      pmt_gain=backend_params.pmt_gain,
      flashes_per_well=backend_params.flashes_per_well,
      kinetic_settings=backend_params.kinetic_settings,
      spectrum_settings=backend_params.spectrum_settings,
      emission_wavelengths=backend_params.emission_wavelengths,
      cuvette=backend_params.cuvette,
      speed_read=False,
      settling_time=backend_params.settling_time,
    )
    await self._set_clear()
    await self._set_read_stage(settings)

    if not backend_params.cuvette:
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
    await self._driver.wait_for_idle(timeout=backend_params.timeout)
    dicts = await self._transfer_data(settings)
    return [
      LuminescenceResult(
        data=d["data"],
        temperature=d["temperature"],
        timestamp=d["time"],
      )
      for d in dicts
    ]


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class SpectraMaxM5(Resource, Device):
  """Molecular Devices SpectraMax M5 plate reader.

  Supports absorbance, fluorescence, and luminescence capabilities.
  """

  def __init__(
    self,
    name: str,
    port: str,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    driver = MolecularDevicesDriver(
      port=port, human_readable_device_name="Molecular Devices SpectraMax M5"
    )
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Molecular Devices SpectraMax M5",
    )
    Device.__init__(self, driver=driver)
    self._driver: MolecularDevicesDriver = driver
    self.absorbance = Absorbance(backend=MolecularDevicesAbsorbanceBackend(driver))
    self.luminescence = Luminescence(backend=SpectraMaxM5LuminescenceBackend(driver))
    self.fluorescence = Fluorescence(backend=SpectraMaxM5FluorescenceBackend(driver))
    self.tc = TemperatureController(backend=MolecularDevicesTemperatureBackend(driver))
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
