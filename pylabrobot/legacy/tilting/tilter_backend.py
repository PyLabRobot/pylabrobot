"""Legacy. Use pylabrobot.capabilities.tilting instead."""

from abc import ABCMeta, abstractmethod

from pylabrobot.legacy.machines.backend import MachineBackend


class TiltModuleError(Exception):
  """Error raised by a tilt module backend."""


class TilterBackend(MachineBackend, metaclass=ABCMeta):
  """A tilt module backend."""

  @abstractmethod
  async def set_angle(self, angle: float):
    """Set the tilt module to rotate by a given angle.

    Args:
      angle: The angle to rotate by, in degrees. Clockwise. 0 is horizontal.
    """
