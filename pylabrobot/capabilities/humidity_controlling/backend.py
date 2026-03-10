from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class HumidityControllerBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for humidity controllers."""

  @property
  @abstractmethod
  def supports_humidity_control(self) -> bool:
    """Whether this backend can set humidity (vs read-only monitoring)."""

  @abstractmethod
  async def set_humidity(self, humidity: float):
    """Set the target humidity as a fraction 0.0-1.0."""

  @abstractmethod
  async def get_current_humidity(self) -> float:
    """Get the current humidity as a fraction 0.0-1.0."""
