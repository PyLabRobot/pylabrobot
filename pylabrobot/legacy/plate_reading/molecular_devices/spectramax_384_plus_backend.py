"""Legacy. Use pylabrobot.molecular_devices.spectramax.SpectraMax384PlusBackend instead."""

from typing import Dict, List, Optional, Union

from pylabrobot.molecular_devices.spectramax.backend import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  PmtGain,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)
from pylabrobot.molecular_devices.spectramax.backend import MolecularDevicesDriver
from pylabrobot.molecular_devices.spectramax.spectramax_384_plus import (
  SpectraMax384PlusAbsorbanceBackend,
)
from pylabrobot.resources.plate import Plate

from .backend import MolecularDevicesBackend


class MolecularDevicesSpectraMax384PlusBackend(MolecularDevicesBackend):
  """Legacy. Use pylabrobot.molecular_devices.spectramax.SpectraMax384Plus instead."""

  def _make_driver(self, port: str):
    return MolecularDevicesDriver(
      port=port, human_readable_device_name="Molecular Devices SpectraMax 384 Plus"
    )

  def __init__(self, port: str) -> None:
    super().__init__(port)
    # Override the absorbance backend with the 384-specific one
    self._absorbance = SpectraMax384PlusAbsorbanceBackend(self._driver)

  async def read_fluorescence(  # type: ignore[override]
    self,
    plate: "Plate",
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional["ShakeSettings"] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 10,
    kinetic_settings: Optional["KineticSettings"] = None,
    spectrum_settings: Optional["SpectrumSettings"] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    raise NotImplementedError("Fluorescence reading is not supported.")

  async def read_luminescence(  # type: ignore[override]
    self,
    plate: "Plate",
    emission_wavelengths: List[int],
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional["ShakeSettings"] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 0,
    kinetic_settings: Optional["KineticSettings"] = None,
    spectrum_settings: Optional["SpectrumSettings"] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    raise NotImplementedError("Luminescence reading is not supported.")

  async def read_fluorescence_polarization(
    self,
    plate: "Plate",
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional["ShakeSettings"] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 10,
    kinetic_settings: Optional["KineticSettings"] = None,
    spectrum_settings: Optional["SpectrumSettings"] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    raise NotImplementedError("Fluorescence polarization reading is not supported.")

  async def read_time_resolved_fluorescence(
    self,
    plate: "Plate",
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    delay_time: int,
    integration_time: int,
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ONCE,
    shake_settings: Optional["ShakeSettings"] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 50,
    kinetic_settings: Optional["KineticSettings"] = None,
    spectrum_settings: Optional["SpectrumSettings"] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[Dict]:
    raise NotImplementedError("Time-resolved fluorescence reading is not supported.")
