from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .backend import (
  MolecularDevicesAbsorbanceBackend,
  MolecularDevicesDriver,
  MolecularDevicesSettings,
  MolecularDevicesTemperatureBackend,
)


class SpectraMax384PlusAbsorbanceBackend(MolecularDevicesAbsorbanceBackend):
  """Absorbance backend for Molecular Devices SpectraMax 384 Plus plate readers.

  Overrides ``_set_readtype`` (simpler CUV/PLA), and no-ops
  ``_set_nvram`` / ``_set_tag``.
  """

  async def _set_readtype(self, settings: MolecularDevicesSettings) -> None:
    cmd = f"!READTYPE {'CUV' if settings.cuvette else 'PLA'}"
    await self.driver.send_command(cmd, num_res_fields=1)

  async def _set_nvram(self, settings: MolecularDevicesSettings) -> None:
    pass

  async def _set_tag(self, settings: MolecularDevicesSettings) -> None:
    pass


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class SpectraMax384Plus(Resource, Device):
  """Molecular Devices SpectraMax 384 Plus plate reader. Absorbance only."""

  def __init__(
    self,
    name: str,
    port: str,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    driver = MolecularDevicesDriver(
      port=port, human_readable_device_name="Molecular Devices SpectraMax 384 Plus"
    )
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Molecular Devices SpectraMax 384 Plus",
    )
    Device.__init__(self, driver=driver)
    self.driver: MolecularDevicesDriver = driver
    self.absorbance = Absorbance(backend=SpectraMax384PlusAbsorbanceBackend(driver))
    self.tc = TemperatureController(backend=MolecularDevicesTemperatureBackend(driver))
    self._capabilities = [self.absorbance, self.tc]

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
