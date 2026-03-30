from abc import ABCMeta, abstractmethod

from pylabrobot.capabilities.capability import CapabilityBackend


class ShakerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for shaking devices."""

  @abstractmethod
  async def start_shaking(self, speed: float):
    """Start shaking at the given speed in RPM."""

  @abstractmethod
  async def stop_shaking(self):
    """Stop shaking."""

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
