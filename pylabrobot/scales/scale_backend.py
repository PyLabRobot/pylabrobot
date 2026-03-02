from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


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

  # Deprecated: for backward compatibility
  async def get_weight(self) -> float:
    """Deprecated: Use read_weight() instead.

    Get the weight in grams"""
    import warnings

    warnings.warn(
      "get_weight() is deprecated and will be removed in 2026-03. Use read_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.read_weight()
