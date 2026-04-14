from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend


class LoadingTrayBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for loading tray devices."""

  @abstractmethod
  async def open(self, backend_params: Optional[BackendParams] = None):
    """Open the loading tray."""

  @abstractmethod
  async def close(self, backend_params: Optional[BackendParams] = None):
    """Close the loading tray."""
