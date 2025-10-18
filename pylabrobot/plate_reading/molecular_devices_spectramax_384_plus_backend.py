from typing import List

from .molecular_devices_backend import MolecularDevicesBackend, MolecularDevicesSettings


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

  async def read_fluorescence(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Fluorescence reading is not supported.")

  async def read_luminescence(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Luminescence reading is not supported.")

  async def read_fluorescence_polarization(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Fluorescence polarization reading is not supported.")

  async def read_time_resolved_fluorescence(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Time-resolved fluorescence reading is not supported.")
