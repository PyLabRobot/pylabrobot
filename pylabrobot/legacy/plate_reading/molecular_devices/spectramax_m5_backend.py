"""Legacy. Use pylabrobot.molecular_devices.spectramax.SpectraMaxM5Backend instead."""

from .backend import MolecularDevicesBackend


class MolecularDevicesSpectraMaxM5Backend(MolecularDevicesBackend):
  """Legacy. Use pylabrobot.molecular_devices.spectramax.SpectraMaxM5Backend instead."""

  def __init__(self, port: str) -> None:
    super().__init__(port)
