from pylabrobot.capabilities.plate_reading.absorbance import AbsorbanceCapability
from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .backend import MolecularDevicesBackend, MolecularDevicesSettings


class SpectraMax384PlusBackend(MolecularDevicesBackend):
  """Backend for Molecular Devices SpectraMax 384 Plus plate readers.

  Absorbance only. Overrides ``_set_readtype`` (simpler CUV/PLA), and no-ops
  ``_set_nvram`` / ``_set_tag``.
  """

  def __init__(self, port: str) -> None:
    super().__init__(port, human_readable_device_name="Molecular Devices SpectraMax 384 Plus")

  async def _set_readtype(self, settings: MolecularDevicesSettings) -> None:
    cmd = f"!READTYPE {'CUV' if settings.cuvette else 'PLA'}"
    await self.send_command(cmd, num_res_fields=1)

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
    backend = SpectraMax384PlusBackend(port=port)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Molecular Devices SpectraMax 384 Plus",
    )
    Device.__init__(self, driver=backend)
    self._driver: SpectraMax384PlusBackend = backend
    self.absorbance = AbsorbanceCapability(backend=backend)
    self.tc = TemperatureControlCapability(backend=backend)
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

  async def open(self) -> None:
    await self._driver.open()

  async def close(self) -> None:
    await self._driver.close()
