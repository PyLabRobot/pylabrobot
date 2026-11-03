from .backend import MolecularDevicesBackend


class MolecularDevicesSpectraMaxM5Backend(MolecularDevicesBackend):
  """Backend for Molecular Devices SpectraMax M5 plate readers."""

  def __init__(self, port: str) -> None:
    super().__init__(port)
