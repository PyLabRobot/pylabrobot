from pylabrobot.resources import Coordinate, PlateHolder

from .base import MolecularDevicesPlateReader, MolecularDevicesSettings


class SpectraMax384Plus(MolecularDevicesPlateReader):
  """Molecular Devices SpectraMax 384 Plus plate reader. Absorbance only.

  Overrides ``_set_readtype`` (simpler CUV/PLA), and no-ops ``_set_nvram`` / ``_set_tag``.
  """

  def __init__(self, name: str, port: str):
    super().__init__(port=port, human_readable_device_name="Molecular Devices SpectraMax 384 Plus")
    self.loading_tray = PlateHolder(
      name=name + "_loading_tray",
      size_x=127.76,
      size_y=85.48,
      size_z=0,  # TODO: measure
      pedestal_size_z=0,  # TODO: measure
      child_location=Coordinate.zero(),  # TODO: measure
    )

  async def _set_readtype(self, settings: MolecularDevicesSettings) -> None:
    cmd = f"!READTYPE {'CUV' if settings.cuvette else 'PLA'}"
    await self.send_command(cmd, num_res_fields=1)

  async def _set_nvram(self, settings: MolecularDevicesSettings) -> None:
    pass

  async def _set_tag(self, settings: MolecularDevicesSettings) -> None:
    pass
