from typing import List, Tuple

from .molecular_devices_backend import (
  MolecularDevicesBackend,
  MolecularDevicesSettings
)


class MolecularDevicesSpectraMax384PlusBackend(MolecularDevicesBackend):
  """Backend for Molecular Devices SpectraMax 384 Plus plate readers."""

  def __init__(self, port: str, res_term_char: bytes = b'>') -> None:
    super().__init__(port, res_term_char)


  def _get_readtype_command(self, settings: MolecularDevicesSettings) -> Tuple[str, int]:
    """Get the READTYPE command and the expected number of response fields."""
    cmd = f"!READTYPE {'CUV' if settings.cuvette else 'PLA'}"
    return (cmd, 1)

  def _get_nvram_commands(self, settings):
    return [None]

  def _get_tag_command(self, settings):
    pass

  async def read_fluorescence(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Fluorescence reading is not supported.")

  async def read_luminescence(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Luminescence reading is not supported.")

  async def read_fluorescence_polarization(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Fluorescence polarization reading is not supported.")

  async def read_time_resolved_fluorescence(self, *args, **kwargs) -> List[List[float]]:
    raise NotImplementedError("Time-resolved fluorescence reading is not supported.")

