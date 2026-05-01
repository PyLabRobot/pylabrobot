from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

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

  async def _wait_for_access_state(
    self,
    predicate: Callable[[PlateAccessState], bool],
    timeout: float = 30.0,
    poll_interval: float = 0.1,
    description: str = "plate access state",
  ) -> PlateAccessState:
    """Wait for a normalized plate-access state predicate to become true."""
    deadline = time.monotonic() + timeout
    while True:
      state = await self.backend.get_access_state()
      if predicate(state):
        return state
      if time.monotonic() >= deadline:
        raise TimeoutError(f"Timed out waiting for {description}.")
      await asyncio.sleep(poll_interval)

  def _remaining_timeout(self, deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())

  @need_capability_ready
  async def open_source_plate(
    self,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Present the source-side access path and return the final access state."""
    deadline = time.monotonic() + timeout
    await self.backend.open_source_plate(timeout=self._remaining_timeout(deadline))
    return await self._wait_for_access_state(
      lambda state: state.source_access_open is True,
      timeout=self._remaining_timeout(deadline),
      poll_interval=poll_interval,
      description="source access to open",
    )

  @need_capability_ready
  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Retract the source-side access path and return the final access state."""
    deadline = time.monotonic() + timeout
    barcode_result = await self.backend.close_source_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=self._remaining_timeout(deadline),
    )
    state = await self._wait_for_access_state(
      lambda state: state.source_access_closed is True,
      timeout=self._remaining_timeout(deadline),
      poll_interval=poll_interval,
      description="source access to close",
    )
    if barcode_result not in (None, ""):
      state.raw = {**state.raw, "barcode": str(barcode_result)}
    return state

  @need_capability_ready
  async def open_destination_plate(
    self,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Present the destination-side access path and return the final access state."""
    deadline = time.monotonic() + timeout
    await self.backend.open_destination_plate(timeout=self._remaining_timeout(deadline))
    return await self._wait_for_access_state(
      lambda state: state.destination_access_open is True,
      timeout=self._remaining_timeout(deadline),
      poll_interval=poll_interval,
      description="destination access to open",
    )

  @need_capability_ready
  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Retract the destination-side access path and return the final access state."""
    deadline = time.monotonic() + timeout
    barcode_result = await self.backend.close_destination_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=self._remaining_timeout(deadline),
    )
    state = await self._wait_for_access_state(
      lambda state: state.destination_access_closed is True,
      timeout=self._remaining_timeout(deadline),
      poll_interval=poll_interval,
      description="destination access to close",
    )
    if barcode_result not in (None, ""):
      state.raw = {**state.raw, "barcode": str(barcode_result)}
    return state

  @need_capability_ready
  async def close_door(
    self,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Close the machine door and return the final access state."""
    deadline = time.monotonic() + timeout
    await self.backend.close_door(timeout=self._remaining_timeout(deadline))
    return await self._wait_for_access_state(
      lambda state: state.door_closed is True,
      timeout=self._remaining_timeout(deadline),
      poll_interval=poll_interval,
      description="door to close",
    )
