from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.serializer import SerializableMixin


class PeelerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for peeling devices."""

  @abstractmethod
  async def peel(self, backend_params: Optional[SerializableMixin] = None):
    """Run an automated de-seal cycle."""

  @abstractmethod
  async def restart(self, backend_params: Optional[SerializableMixin] = None):
    """Restart the peeler machine."""
