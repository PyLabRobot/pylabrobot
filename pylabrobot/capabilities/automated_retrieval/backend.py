from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.resources import Plate, PlateHolder


class AutomatedRetrievalBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for automated plate retrieval/storage devices."""

  @abstractmethod
  async def fetch_plate_to_loading_tray(self, plate: Plate):
    """Retrieve a plate from storage and place it on the loading tray."""

  @abstractmethod
  async def store_plate(self, plate: Plate, site: PlateHolder):
    """Store a plate from the loading tray into the given site."""
