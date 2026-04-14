from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend


class ShakerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for shaking devices."""

  @abstractmethod
  async def shake(
    self,
    speed: float,
    duration: float,
    backend_params: Optional[BackendParams] = None,
  ):
    """Shake at the given speed for the given duration.

    Args:
      speed: Speed in RPM.
      duration: Duration in seconds.
      backend_params: Backend-specific parameters.
    """

  @property
  @abstractmethod
  def supports_locking(self) -> bool:
    """Whether this backend supports locking the plate."""

  @abstractmethod
  async def lock_plate(self):
    """Lock the plate."""

  @abstractmethod
  async def unlock_plate(self):
    """Unlock the plate."""


class HasContinuousShaking(metaclass=ABCMeta):
  """Mixin for shakers that support independent start/stop control.

  Similar to :class:`~pylabrobot.capabilities.arms.backend.HasJoints` for arms,
  this mixin adds optional capability to backends that support continuous
  (indefinite) shaking with explicit start and stop commands.
  """

  @abstractmethod
  async def start_shaking(self, speed: float):
    """Start shaking indefinitely at the given speed in RPM."""

  @abstractmethod
  async def stop_shaking(self):
    """Stop shaking."""
