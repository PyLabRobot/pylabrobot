"""Legacy. Use pylabrobot.molecular_devices.spectramax instead."""

from typing import Dict, List, Optional, Tuple, Union

from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.molecular_devices.spectramax.backend import (  # noqa: F401
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesAbsorbanceBackend,
  MolecularDevicesDriver,
  MolecularDevicesError,
  MolecularDevicesFirmwareError,
  MolecularDevicesHardwareError,
  MolecularDevicesMotionError,
  MolecularDevicesNVRAMError,
  MolecularDevicesSettings,
  MolecularDevicesTemperatureBackend,
  MolecularDevicesUnrecognizedCommandError,
  PmtGain,
  ReadMode,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)
from pylabrobot.molecular_devices.spectramax.spectramax_m5 import (
  SpectraMaxM5FluorescenceBackend,
  SpectraMaxM5LuminescenceBackend,
)
from pylabrobot.resources.plate import Plate


class MolecularDevicesBackend(PlateReaderBackend):
  """Legacy. Use pylabrobot.molecular_devices.spectramax instead.

  Delegates to the new capability-based backend, adapting read method signatures
  and return types (List[Dict]) for backward compatibility.
  """

  def __init__(self, port: str) -> None:
    self.driver = self._make_driver(port)
    self._absorbance = MolecularDevicesAbsorbanceBackend(self.driver)
    self._temperature = MolecularDevicesTemperatureBackend(self.driver)
    self._fluorescence = SpectraMaxM5FluorescenceBackend(self.driver)
    self._luminescence = SpectraMaxM5LuminescenceBackend(self.driver)

  def _make_driver(self, port: str):
    return MolecularDevicesDriver(port=port)

  # -- PlateReaderBackend / MachineBackend interface -----------------------

  async def setup(self) -> None:
    await self.driver.setup()

  async def stop(self) -> None:
    await self.driver.stop()

  async def open(self) -> None:
    await self.driver.open()

  async def close(self, plate=None) -> None:
    await self.driver.close()

  async def send_command(self, *args, **kwargs):
    return await self.driver.send_command(*args, **kwargs)

  def serialize(self) -> dict:
    return dict(self.driver.serialize())

  # -- Bridged internals (must be explicit for class-level @patch) ---------

  async def _read_now(self):
    return await self._absorbance._read_now()

  async def _wait_for_idle(self, **kwargs):
    return await self.driver.wait_for_idle(**kwargs)

  async def _transfer_data(self, *args, **kwargs):
    return await self._absorbance._transfer_data(*args, **kwargs)

  # -- Legacy read methods (delegate to _new, convert results) -------------

  async def read_absorbance(  # type: ignore[override]
    self,
    plate: Plate,
    wavelengths: List[Union[int, Tuple[int, bool]]],
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    speed_read: bool = False,
    path_check: bool = False,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    wl0 = wavelengths[0]
    wavelength = wl0[0] if isinstance(wl0, tuple) else wl0
    params = MolecularDevicesAbsorbanceBackend.AbsorbanceParams(
      wavelengths=wavelengths,
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      speed_read=speed_read,
      path_check=path_check,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      cuvette=cuvette,
      settling_time=settling_time,
      timeout=timeout,
    )
    results = await self._absorbance.read_absorbance(
      plate=plate,
      wells=[],
      wavelength=wavelength,
      backend_params=params,
    )
    return [
      {
        "wavelength": r.wavelength,
        "data": r.data,
        "temperature": r.temperature,
        "time": r.timestamp,
      }
      for r in results
    ]

  async def read_fluorescence(  # type: ignore[override]
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
    params = SpectraMaxM5FluorescenceBackend.FluorescenceParams(
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
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
      cuvette=cuvette,
      settling_time=settling_time,
      timeout=timeout,
    )
    results = await self._fluorescence.read_fluorescence(
      plate=plate,
      wells=[],
      excitation_wavelength=excitation_wavelengths[0],
      emission_wavelength=emission_wavelengths[0],
      focal_height=0,
      backend_params=params,
    )
    return [
      {
        "ex_wavelength": r.excitation_wavelength,
        "em_wavelength": r.emission_wavelength,
        "data": r.data,
        "temperature": r.temperature,
        "time": r.timestamp,
      }
      for r in results
    ]

  async def read_luminescence(  # type: ignore[override]
    self,
    plate: Plate,
    emission_wavelengths: List[int],
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
  ) -> List[Dict]:
    params = SpectraMaxM5LuminescenceBackend.LuminescenceParams(
      emission_wavelengths=emission_wavelengths,
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
      cuvette=cuvette,
      settling_time=settling_time,
      timeout=timeout,
    )
    results = await self._luminescence.read_luminescence(
      plate=plate,
      wells=[],
      focal_height=0,
      backend_params=params,
    )
    return [{"data": r.data, "temperature": r.temperature, "time": r.timestamp} for r in results]

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
    return await self._fluorescence.read_fluorescence_polarization(
      plate=plate,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
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
      cuvette=cuvette,
      settling_time=settling_time,
      timeout=timeout,
    )

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
    return await self._fluorescence.read_time_resolved_fluorescence(
      plate=plate,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      delay_time=delay_time,
      integration_time=integration_time,
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
      cuvette=cuvette,
      settling_time=settling_time,
      timeout=timeout,
    )
