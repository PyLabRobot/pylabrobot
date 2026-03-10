from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class TiltModuleError(Exception):
  """Error raised by a tilt module backend."""


class TilterBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for tilting devices."""

  @abstractmethod
  async def set_angle(self, angle: float):
    """Set the tilt angle.

    Args:
      angle: The angle in degrees. 0 is horizontal.
    """
