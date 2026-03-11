from abc import ABCMeta, abstractmethod

from pylabrobot.legacy.machines.backend import MachineBackend


class PeelerBackend(MachineBackend, metaclass=ABCMeta):
  """Legacy. Use pylabrobot.capabilities.peeling.PeelerBackend instead."""

  @abstractmethod
  async def peel(self):
    """Run an automated de-seal cycle."""

  @abstractmethod
  async def restart(self):
    """Restart the peeler machine."""
