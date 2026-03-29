from abc import ABCMeta, abstractmethod

from pylabrobot.capabilities.capability import CapabilityBackend


class TemperatureControllerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for temperature controllers."""

  @property
  @abstractmethod
  def supports_active_cooling(self) -> bool:
    """Whether this backend can actively cool below the current temperature."""

  @abstractmethod
  async def set_temperature(self, temperature: float):
    """Set the temperature of the temperature controller in Celsius."""

  @abstractmethod
  async def request_current_temperature(self) -> float:
    """Get the current temperature of the temperature controller in Celsius"""

  @abstractmethod
  async def deactivate(self):
    """Deactivate the temperature controller."""
