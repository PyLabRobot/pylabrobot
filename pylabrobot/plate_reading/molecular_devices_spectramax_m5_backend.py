from .molecular_devices_backend import MolecularDevicesBackend


class MolecularDevicesSpectraMaxM5Backend(MolecularDevicesBackend):
  """Backend for Molecular Devices SpectraMax M5 plate readers."""

  def __init__(self, port: str, res_term_char: bytes = b'>') -> None:
    super().__init__(port, res_term_char)
