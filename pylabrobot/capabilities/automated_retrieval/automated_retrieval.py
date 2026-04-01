from pylabrobot.capabilities.capability import Capability
from pylabrobot.resources import Plate, PlateHolder

from .backend import AutomatedRetrievalBackend


class AutomatedRetrieval(Capability):
  """Automated plate retrieval/storage capability.

  See :doc:`/user_guide/capabilities/automated-retrieval` for a walkthrough.
  """

  def __init__(self, backend: AutomatedRetrievalBackend):
    super().__init__(backend=backend)
    self.backend: AutomatedRetrievalBackend = backend

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    """Retrieve a plate from storage and place it on the loading tray."""
    await self.backend.fetch_plate_to_loading_tray(plate)

  async def store_plate(self, plate: Plate, site: PlateHolder):
    """Store a plate from the loading tray into the given site."""
    await self.backend.store_plate(plate, site)

  async def _on_stop(self):
    await super()._on_stop()
