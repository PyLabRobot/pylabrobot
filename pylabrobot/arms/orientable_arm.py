from typing import List, Optional, Union

from pylabrobot.arms.arm import _BaseArm, _PickedUpState, _resolve_direction, GripOrientation
from pylabrobot.arms.backend import OrientableArmBackend
from pylabrobot.arms.standard import GripDirection
from pylabrobot.resources import Coordinate, Resource, ResourceHolder, ResourceStack
from pylabrobot.resources.rotation import Rotation
from pylabrobot.serializer import SerializableMixin


class OrientableArm(_BaseArm):
  """An arm with rotation capability. E.g. Hamilton iSWAP."""

  def __init__(self, backend: OrientableArmBackend, reference_resource: Resource):
    super().__init__(backend=backend, reference_resource=reference_resource)
    self.backend: OrientableArmBackend = backend  # type: ignore[assignment]

  @staticmethod
  def _resource_width_for_direction(resource: Resource, direction: float) -> float:
    # TODO: resource rotation is not taken into account here.
    if direction % 180 == 0:
      return resource.get_absolute_size_x()
    else:
      return resource.get_absolute_size_y()

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    direction: GripOrientation = 0.0,
    backend_params: Optional[SerializableMixin] = None,
  ):
    dir_degrees = _resolve_direction(direction)
    self._begin_holding(resource_width)
    await self.backend.pick_up_at_location(
      location=location,
      direction=dir_degrees,
      resource_width=resource_width,
      backend_params=backend_params,
    )

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    direction: GripOrientation = GripDirection.FRONT,
    backend_params: Optional[SerializableMixin] = None,
  ):
    location, pickup_distance_from_top = self._prepare_pickup(
      resource, offset, pickup_distance_from_top
    )
    dir_degrees = _resolve_direction(direction)
    resource_width = self._resource_width_for_direction(resource, dir_degrees)
    await self.pick_up_at_location(location, resource_width, dir_degrees, backend_params)
    self._picked_up = _PickedUpState(
      resource=resource,
      offset=offset,
      pickup_distance_from_top=pickup_distance_from_top,
      resource_width=resource_width,
      rotation=Rotation(z=dir_degrees),
    )
    self._state_updated()

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: GripOrientation,
    backend_params: Optional[SerializableMixin] = None,
  ):
    if not self.holding:
      raise RuntimeError("Not holding anything")
    await self.backend.drop_at_location(
      location=location,
      direction=_resolve_direction(direction),
      resource_width=self._holding_resource_width,
      backend_params=backend_params,
    )
    self._end_holding()

  async def drop_resource(
    self,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    direction: GripOrientation = GripDirection.FRONT,
    backend_params: Optional[SerializableMixin] = None,
  ):
    resource = self._prepare_drop(destination)
    drop_dir = _resolve_direction(direction)
    rotation_applied_by_move = (drop_dir - self._picked_up.rotation.z) % 360
    location, rotation = self._compute_drop(
      resource=resource,
      destination=destination,
      offset=offset,
      pickup_distance_from_top=self._picked_up.pickup_distance_from_top,
      rotation_applied_by_move=rotation_applied_by_move,
    )
    await self.drop_at_location(location, drop_dir, backend_params)
    self._finalize_drop(resource, destination, rotation)

  async def move_to_location(
    self,
    location: Coordinate,
    direction: GripOrientation = 0.0,
    backend_params: Optional[SerializableMixin] = None,
  ):
    await self.backend.move_to_location(
      location=location,
      direction=_resolve_direction(direction),
      backend_params=backend_params,
    )

  async def move_picked_up_resource(
    self,
    to: Coordinate,
    direction: GripOrientation,
    offset: Coordinate = Coordinate.zero(),
    backend_params: Optional[SerializableMixin] = None,
  ):
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    dir_degrees = _resolve_direction(direction)
    location = self._move_location(
      self._picked_up.resource, to, offset, self._picked_up.pickup_distance_from_top
    )
    await self.backend.move_to_location(
      location=location, direction=dir_degrees, backend_params=backend_params
    )

  async def move_resource(
    self,
    resource: Resource,
    to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    pickup_direction: GripOrientation = GripDirection.FRONT,
    drop_direction: GripOrientation = GripDirection.FRONT,
    pickup_backend_params: Optional[SerializableMixin] = None,
    drop_backend_params: Optional[SerializableMixin] = None,
  ):
    await self.pick_up_resource(
      resource=resource,
      offset=pickup_offset,
      pickup_distance_from_top=pickup_distance_from_top,
      direction=pickup_direction,
      backend_params=pickup_backend_params,
    )
    for loc in intermediate_locations or []:
      await self.move_picked_up_resource(to=loc)
    await self.drop_resource(
      destination=to,
      offset=destination_offset,
      direction=drop_direction,
      backend_params=drop_backend_params,
    )
