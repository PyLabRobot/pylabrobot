"""Tecan EVO RoMa arm frontend.

Overrides the generic GripperArm to route pick/drop through the
carrier-based backend methods, since the RoMa Z trajectory requires
carrier-level attributes (roma_z_safe, roma_z_end) that cannot be
derived from a raw Coordinate alone.
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from pylabrobot.arms.arm import GripperArm, _PickedUpState
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import (
  Coordinate,
  Resource,
  ResourceHolder,
  ResourceStack,
)

from pylabrobot.tecan.evo.roma_backend import EVORoMaBackend

logger = logging.getLogger(__name__)


class TecanGripperArm(GripperArm):
  """GripperArm for the Tecan EVO RoMa.

  Routes pick_up_resource and drop_resource through the backend's
  carrier-based methods which compute RoMa X/Y/Z from carrier attributes.
  Resource tracking (unassign/assign) is handled by the base class.

  Usage::

    await evo.arm.move_resource(plate, carrier_dst[0])
  """

  backend: EVORoMaBackend

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ):
    resource_width = self._resource_width(resource)
    self._begin_holding(resource_width)

    await self.backend.pick_up_from_carrier(resource, backend_params=backend_params)

    pickup_distance_from_top = self._resolve_pickup_distance(resource, pickup_distance_from_top)
    self._picked_up = _PickedUpState(
      resource=resource,
      offset=offset,
      pickup_distance_from_top=pickup_distance_from_top,
      resource_width=resource_width,
    )
    self._state_updated()

  async def drop_resource(
    self,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    backend_params: Optional[BackendParams] = None,
  ):
    resource = self._prepare_drop(destination)
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")

    # Compute destination offset for the backend
    if isinstance(destination, ResourceHolder):
      dst_offset = destination.get_location_wrt(self._reference_resource)
    elif isinstance(destination, Coordinate):
      dst_offset = destination
    else:
      dst_offset = destination.get_location_wrt(self._reference_resource)

    await self.backend.drop_at_carrier(resource, dst_offset, backend_params=backend_params)

    # Use base class resource tracking
    self._finalize_drop(resource, destination, 0)
