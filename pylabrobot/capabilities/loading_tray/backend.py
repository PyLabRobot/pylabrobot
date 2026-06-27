from abc import ABCMeta, abstractmethod
from typing import TYPE_CHECKING, Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend

if TYPE_CHECKING:
  from pylabrobot.resources.resource import Resource


class LoadingTrayBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for loading tray devices."""

  @abstractmethod
  async def open(self, backend_params: Optional[BackendParams] = None):
    """Open the loading tray."""

  @abstractmethod
  async def close(
    self,
    backend_params: Optional[BackendParams] = None,
    plate: Optional["Resource"] = None,
  ):
    """Close the loading tray.

    Args:
      plate: the resource currently held by the tray, if any. Backends that need the labware
        geometry during the close motion (e.g. to give a tall plate enough clearance) can use it;
        others ignore it.
    """
