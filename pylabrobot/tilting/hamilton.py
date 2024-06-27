from pylabrobot.tilting.tilter import Tilter
from pylabrobot.tilting.hamilton_backend import HamiltonTiltModuleBackend


def HamiltonTiltModule(  # pylint: disable=invalid-name
  name: str,
  com_port: str,
  write_timeout: float = 10, timeout: float = 10
) -> Tilter:
  return Tilter(
    name=name,
    backend=HamiltonTiltModuleBackend(
      com_port=com_port, write_timeout=write_timeout, timeout=timeout
    ),
    size_x=132,
    size_y=92.57,
    size_z=85.81,
  )
