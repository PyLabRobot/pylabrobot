from abc import ABCMeta, abstractmethod
import warnings

from pylabrobot.machines.backend import MachineBackend


class ShakerBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a shaker machine"""

  @abstractmethod
  async def start_shaking(self, speed: float):
    """Start shaking at the given speed.

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
    """

  async def shake(self, speed: float):
    """Deprecated alias for ``start_shaking``.

    Backends should implement ``start_shaking``. This method exists for backwards compatibility.
    """

    warnings.warn(
      "ShakerBackend.shake() is deprecated. Use start_shaking() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    await self.start_shaking(speed=speed)

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
