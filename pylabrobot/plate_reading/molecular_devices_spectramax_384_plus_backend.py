from typing import Dict, List, Optional, Tuple, Union

from pylabrobot.resources.plate import Plate

from .molecular_devices_backend import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesBackend,
  MolecularDevicesSettings,
  PmtGain,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)


class MolecularDevicesSpectraMax384PlusBackend(MolecularDevicesBackend):
  """Backend for Molecular Devices SpectraMax 384 Plus plate readers."""

  def __init__(self, port: str) -> None:
    super().__init__(port)

  async def _set_readtype(self, settings: MolecularDevicesSettings) -> None:
    """Set the READTYPE command and the expected number of response fields."""
    cmd = f"!READTYPE {'CUV' if settings.cuvette else 'PLA'}"
    await self.send_command(cmd, num_res_fields=1)

  async def _set_nvram(self, settings: MolecularDevicesSettings) -> None:
    pass

  async def _set_tag(self, settings: MolecularDevicesSettings) -> None:
    pass

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
