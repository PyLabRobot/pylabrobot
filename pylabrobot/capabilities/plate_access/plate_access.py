from __future__ import annotations

from typing import Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready

from .backend import PlateAccessBackend, PlateAccessState


class PlateAccess(Capability):
  """Plate access capability.

  See :doc:`/user_guide/capabilities/plate-access` for a walkthrough.
  """

  def __init__(self, backend: PlateAccessBackend):
    super().__init__(backend=backend)
    self.backend: PlateAccessBackend = backend

  @need_capability_ready
  async def lock(self, app: Optional[str] = None, owner: Optional[str] = None) -> None:
    """Lock the machine for exclusive access."""
    await self.backend.lock(app=app, owner=owner)

  @need_capability_ready
  async def unlock(self) -> None:
    """Release the machine lock held by this client."""
    await self.backend.unlock()

  @need_capability_ready
  async def get_access_state(self) -> PlateAccessState:
    """Poll the current access state."""
    return await self.backend.get_access_state()

  @need_capability_ready
  async def open_source_plate(self) -> None:
    """Present the source-side access path."""
    await self.backend.open_source_plate()

  @need_capability_ready
  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    """Retract the source-side access path."""
    await self.backend.close_source_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
    )

  @need_capability_ready
  async def open_destination_plate(self) -> None:
    """Present the destination-side access path."""
    await self.backend.open_destination_plate()

  @need_capability_ready
  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    """Retract the destination-side access path."""
    await self.backend.close_destination_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
    )

  @need_capability_ready
  async def close_door(self) -> None:
    """Close the machine door."""
    await self.backend.close_door()
