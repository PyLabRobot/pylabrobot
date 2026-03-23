"""Legacy. Use pylabrobot.agilent instead."""

from typing import Dict, List, Optional

from pylabrobot.agilent.biotek import biotek
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate, Well


class BioTekPlateReaderBackend(PlateReaderBackend):
  """Legacy. Use pylabrobot.agilent.BioTekBackend instead."""

  def __init__(
    self,
    timeout: float = 20,
    device_id: Optional[str] = None,
  ) -> None:
    self._new = biotek.BioTekBackend(timeout=timeout, device_id=device_id)

  # Expose internals for subclass compatibility
  @property
  def io(self):
    return self._new.io

  @io.setter
  def io(self, value):
    self._new.io = value

  @property
  def timeout(self):
    return self._new.timeout

  @timeout.setter
  def timeout(self, value):
    self._new.timeout = value

  @property
  def _plate(self):
    return self._new._plate

  @_plate.setter
  def _plate(self, value):
    self._new._plate = value

  @property
  def _shaking(self):
    return self._new._shaking

  @_shaking.setter
  def _shaking(self, value):
    self._new._shaking = value

  @property
  def _slow_mode(self):
    return self._new._slow_mode

  @_slow_mode.setter
  def _slow_mode(self, value):
    self._new._slow_mode = value

  @property
  def _version(self):
    return self._new._version

  @_version.setter
  def _version(self, value):
    self._new._version = value

  async def setup(self) -> None:
    await self._new.setup()

  async def stop(self) -> None:
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  @property
  def version(self) -> str:
    return self._new.version

  @property
  def abs_wavelength_range(self):
    return self._new.abs_wavelength_range

  @property
  def focal_height_range(self):
    return self._new.focal_height_range

  @property
  def excitation_range(self):
    return self._new.excitation_range

  @property
  def emission_range(self):
    return self._new.emission_range

  @property
  def supports_heating(self) -> bool:
    return self._new.supports_heating

  @property
  def supports_cooling(self) -> bool:
    return self._new.supports_cooling

  @property
  def temperature_range(self):
    return self._new.temperature_range

  async def send_command(self, command, parameter=None, wait_for_response=True, timeout=None):
    return await self._new.send_command(command, parameter, wait_for_response, timeout)

  async def _read_until(self, terminator, timeout=None):
    return await self._new._read_until(terminator, timeout)

  async def get_serial_number(self):
    return await self._new.get_serial_number()

  async def get_firmware_version(self):
    return await self._new.get_firmware_version()

  async def open(self, slow=False):
    return await self._new.open(slow=slow)

  async def close(self, plate=None, slow=False):
    return await self._new.close(plate=plate, slow=slow)

  async def home(self):
    return await self._new.home()

  async def get_current_temperature(self):
    return await self._new.get_current_temperature()

  async def set_temperature(self, temperature):
    return await self._new.set_temperature(temperature)

  async def stop_heating_or_cooling(self):
    return await self._new.stop_heating_or_cooling()

  def _parse_body(self, body):
    return self._new._parse_body(body)

  async def set_plate(self, plate):
    return await self._new.set_plate(plate)

  async def read_absorbance(self, plate: Plate, wells: List[Well], wavelength: int) -> List[Dict]:
    results = await self._new.read_absorbance(plate=plate, wells=wells, wavelength=wavelength)
    return [
      {
        "wavelength": r.wavelength,
        "data": r.data,
        "temperature": r.temperature if r.temperature is not None else float("nan"),
        "time": r.timestamp,
      }
      for r in results
    ]

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float, integration_time: float = 1
  ) -> List[Dict]:
    from pylabrobot.agilent.biotek.biotek import BioTekBackend

    params = BioTekBackend.LuminescenceParams(integration_time=integration_time)
    results = await self._new.read_luminescence(
      plate=plate, wells=wells, focal_height=focal_height, backend_params=params
    )
    return [
      {
        "data": r.data,
        "temperature": r.temperature if r.temperature is not None else float("nan"),
        "time": r.timestamp,
      }
      for r in results
    ]

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict]:
    results = await self._new.read_fluorescence(
      plate=plate,
      wells=wells,
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
    )
    return [
      {
        "ex_wavelength": r.excitation_wavelength,
        "em_wavelength": r.emission_wavelength,
        "data": r.data,
        "temperature": r.temperature if r.temperature is not None else float("nan"),
        "time": r.timestamp,
      }
      for r in results
    ]

  ShakeType = biotek.BioTekBackend.ShakeType

  async def shake(self, shake_type, frequency):
    return await self._new.shake(shake_type, frequency)

  async def stop_shaking(self):
    return await self._new.stop_shaking()
