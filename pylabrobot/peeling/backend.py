from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class PeelerBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a peeler machine"""

  @abstractmethod
  async def peel(self):
    """
    Run an automated de-seal cycle.
    """
    ...

  @abstractmethod
  async def restart(self):
    """Restart the peeler machine."""
    ...
