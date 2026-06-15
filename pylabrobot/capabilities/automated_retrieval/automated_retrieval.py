from typing import Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.resources import Plate, PlateHolder

from .backend import AutomatedRetrievalBackend


class AutomatedRetrieval(Capability):
  """Automated plate retrieval/storage capability.

  See :doc:`/user_guide/capabilities/automated-retrieval` for a walkthrough.
  """

  def __init__(self, backend: AutomatedRetrievalBackend):
    super().__init__(backend=backend)
    self.backend: AutomatedRetrievalBackend = backend

  @need_capability_ready
  async def fetch_plate_to_loading_tray(self, plate: Plate, tray: Optional[int] = None):
    """Retrieve a plate from storage and place it on a loading tray.

    Args:
      plate: The plate to retrieve.
      tray: 0-based index of the loading tray to deliver to. ``None`` selects the
        device's default tray (single-tray devices only accept ``None``/``0``).
    """
    await self.backend.fetch_plate_to_loading_tray(plate, tray=tray)

  @need_capability_ready
  async def store_plate(self, plate: Plate, site: PlateHolder, tray: Optional[int] = None):
    """Store a plate from a loading tray into the given site.

    Args:
      plate: The plate to store.
      site: The destination storage site.
      tray: 0-based index of the loading tray the plate is on. ``None`` selects
        the device's default tray.
    """
    await self.backend.store_plate(plate, site, tray=tray)

  async def _on_stop(self):
    await super()._on_stop()
