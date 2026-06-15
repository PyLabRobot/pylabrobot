from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources import Plate, PlateHolder


def ensure_single_tray(tray: Optional[int]) -> None:
  """Guard for backends with exactly one loading tray.

  Raises ``ValueError`` if ``tray`` is anything other than ``None`` (default) or
  ``0`` (the only tray).
  """
  if tray not in (None, 0):
    raise ValueError(f"This device has a single loading tray; got tray={tray}. Use None or 0.")


class AutomatedRetrievalBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for automated plate retrieval/storage devices."""

  @abstractmethod
  async def fetch_plate_to_loading_tray(self, plate: Plate, tray: Optional[int] = None):
    """Retrieve a plate from storage and place it on a loading tray.

    Args:
      plate: The plate to retrieve.
      tray: 0-based index of the loading tray to deliver the plate to. ``None``
        selects the device's default tray. Devices with a single loading tray
        accept ``None``/``0`` and reject any other value (see
        :func:`ensure_single_tray`).
    """

  @abstractmethod
  async def store_plate(self, plate: Plate, site: PlateHolder, tray: Optional[int] = None):
    """Store a plate from a loading tray into the given site.

    Args:
      plate: The plate to store.
      site: The destination storage site.
      tray: 0-based index of the loading tray the plate is currently on. ``None``
        selects the device's default tray.
    """
