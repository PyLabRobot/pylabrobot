from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class ShakerBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a shaker machine"""

  @abstractmethod
  async def shake(self, speed: float):
    """Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
    """

  @abstractmethod
  async def stop_shaking(self):
    """Stop shaking"""

  @property
  @abstractmethod
  def supports_locking(self) -> bool:
    """Check if the shaker supports locking the plate"""

  @abstractmethod
  async def lock_plate(self):
    """Lock the plate"""

  @abstractmethod
  async def unlock_plate(self):
    """Unlock the plate"""
