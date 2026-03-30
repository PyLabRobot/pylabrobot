"""Legacy. Use pylabrobot.capabilities.weighing.ScaleBackend instead."""

from abc import ABCMeta, abstractmethod

from pylabrobot.legacy.machines.backend import MachineBackend


class ScaleBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a scale"""

  @abstractmethod
  async def zero(self): ...

  @abstractmethod
  async def tare(self): ...

  @abstractmethod
  async def read_weight(self) -> float:
    """Read the weight in grams"""
    ...

  async def get_weight(self) -> float:
    """Deprecated: Use read_weight() instead."""
    import warnings

    warnings.warn(
      "get_weight() is deprecated. Use read_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.read_weight()
