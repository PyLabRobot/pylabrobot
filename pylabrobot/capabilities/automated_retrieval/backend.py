from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources import Plate, PlateHolder


class AutomatedRetrievalBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for automated plate retrieval/storage devices."""

  @abstractmethod
  async def fetch_plate_to_loading_tray(self, plate: Plate, tray_index: Optional[int] = None):
    """Retrieve a plate from storage and place it on a loading tray.

    Args:
      plate: The plate to retrieve.
      tray_index: 0-based index of the loading tray to deliver the plate to. ``None``
        selects the device's default tray. Devices with a single loading tray
        accept ``None``/``0`` and reject any other value.
    """

  @abstractmethod
  async def store_plate(self, plate: Plate, site: PlateHolder, tray_index: Optional[int] = None):
    """Store a plate from a loading tray into the given site.

    Args:
      plate: The plate to store.
      site: The destination storage site.
      tray_index: 0-based index of the loading tray the plate is currently on. ``None``
        selects the device's default tray.
    """
