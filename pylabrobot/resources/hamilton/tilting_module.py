from pylabrobot.liquid_handling.backends.hamilton.tilt_module import HamiltonTiltModuleBackend
from pylabrobot.liquid_handling.resources.tilt_module import TiltModule


def HamiltonTiltModule(  # pylint: disable=invalid-name
    com_port: str, write_timeout: float = 10, timeout: float = 10
) -> TiltModule:
  return TiltModule(
    backend=HamiltonTiltModuleBackend(
      com_port=com_port, write_timeout=write_timeout, timeout=timeout
    ),
    size_x=132,
    size_y=92.57,
    size_z=85.81,
  )
