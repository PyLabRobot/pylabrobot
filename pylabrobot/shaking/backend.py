import warnings
from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class ShakerBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a shaker machine"""

  async def start_shaking(self, speed: float):
    """Start shaking at the given speed.

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
    """
    shake_impl = self.__class__.shake
    if shake_impl is not ShakerBackend.shake:
      await shake_impl(self, speed=speed)
      return
    raise NotImplementedError(
      f"{self.__class__.__name__} must implement start_shaking() (preferred) or shake() (legacy)."
    )

  async def shake(self, speed: float):
    """Deprecated alias for start_shaking."""
    warnings.warn(
      "ShakerBackend.shake() is deprecated and will be removed in a future release. "
      "Use start_shaking() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    start_impl = self.__class__.start_shaking
    if start_impl is not ShakerBackend.start_shaking:
      await start_impl(self, speed=speed)
      return
    raise NotImplementedError(
      f"{self.__class__.__name__} must implement start_shaking() (preferred) or shake() (legacy)."
    )

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
