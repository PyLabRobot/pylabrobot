from abc import ABCMeta, abstractmethod
from pylabrobot.machines.machine import MachineBackend


class TiltModuleError(Exception):
  """ Error raised by a tilt module backend. """


class TilterBackend(MachineBackend, metaclass=ABCMeta):
  """ A tilt module backend. """

  @abstractmethod
  async def set_angle(self, angle: float):
    """ Set the tilt module to rotate by a given angle.

    We assume the rotation anchor is the right side of the module. This may change in the future
    if we integrate other tilt modules.

    Args:
      angle: The angle to rotate by, in degrees. Clockwise. 0 is horizontal.
    """
