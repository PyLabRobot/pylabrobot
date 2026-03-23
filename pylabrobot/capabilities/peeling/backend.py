from abc import ABCMeta, abstractmethod

from pylabrobot.device import DeviceBackend


class PeelerBackend(DeviceBackend, metaclass=ABCMeta):
  """Abstract backend for peeling devices."""

  @abstractmethod
  async def peel(self):
    """Run an automated de-seal cycle."""

  @abstractmethod
  async def restart(self):
    """Restart the peeler machine."""
