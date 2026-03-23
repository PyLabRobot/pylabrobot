"""Legacy. Use pylabrobot.molecular_devices.spectramax instead."""

from typing import Dict, List, Optional, Tuple, Union

from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.molecular_devices.spectramax.backend import (  # noqa: F401
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesError,
  MolecularDevicesFirmwareError,
  MolecularDevicesHardwareError,
  MolecularDevicesMotionError,
  MolecularDevicesNVRAMError,
  MolecularDevicesSettings,
  MolecularDevicesUnrecognizedCommandError,
  PmtGain,
  ReadMode,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)
from pylabrobot.molecular_devices.spectramax.spectramax_m5 import SpectraMaxM5Backend
from pylabrobot.resources.plate import Plate


class MolecularDevicesBackend(PlateReaderBackend):
  """Legacy. Use pylabrobot.molecular_devices.spectramax.MolecularDevicesBackend instead.

  Delegates to the new capability-based backend, adapting read method signatures
  and return types (List[Dict]) for backward compatibility.
  """

  def __init__(self, port: str) -> None:
    self._new: SpectraMaxM5Backend = self._make_new_backend(port)

    # Bridge internal methods so test mocks on self.* intercept calls from _new.
    self._real_send_command = self._new.send_command
    self._real_read_now = self._new._read_now
    self._real_wait_for_idle = self._new._wait_for_idle
    self._real_transfer_data = self._new._transfer_data

    async def _sc(*a, **kw):
      return await self.send_command(*a, **kw)

    async def _rn():
      return await self._read_now()

    async def _wfi(**kw):
      return await self._wait_for_idle(**kw)

    async def _td(*a, **kw):
      return await self._transfer_data(*a, **kw)

    self._new.send_command = _sc
    self._new._read_now = _rn
    self._new._wait_for_idle = _wfi
    self._new._transfer_data = _td

  def _make_new_backend(self, port: str):
    return SpectraMaxM5Backend(port=port)

  # -- PlateReaderBackend / MachineBackend interface -----------------------

  async def setup(self) -> None:
    await self._new.setup()

  async def stop(self) -> None:
    await self._new.stop()

  async def open(self) -> None:
    await self._new.open()

  async def close(self, plate=None) -> None:
    await self._new.close(plate=plate)

  async def send_command(self, *args, **kwargs):
    return await self._real_send_command(*args, **kwargs)

  def serialize(self) -> dict:
    return dict(self._new.serialize())

  # -- Bridged internals (must be explicit for class-level @patch) ---------

  async def _read_now(self):
    return await self._real_read_now()

  async def _wait_for_idle(self, **kwargs):
    return await self._real_wait_for_idle(**kwargs)

  async def _transfer_data(self, *args, **kwargs):
    return await self._real_transfer_data(*args, **kwargs)

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
    results = await self._new.read_absorbance(
      plate=plate,
      wells=[],
      wavelength=wavelength,
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
    results = await self._new.read_fluorescence(
      plate=plate,
      wells=[],
      excitation_wavelength=excitation_wavelengths[0],
      emission_wavelength=emission_wavelengths[0],
      focal_height=0,
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
    results = await self._new.read_luminescence(
      plate=plate,
      wells=[],
      focal_height=0,
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
    return await self._new.read_fluorescence_polarization(
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
    return await self._new.read_time_resolved_fluorescence(
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
