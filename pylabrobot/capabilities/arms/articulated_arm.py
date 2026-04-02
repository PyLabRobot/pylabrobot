from typing import List, Optional, Union

from pylabrobot.capabilities.arms.arm import _BaseArm, _PickedUpState
from pylabrobot.capabilities.arms.backend import ArticulatedGripperArmBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Resource, ResourceHolder, ResourceStack
from pylabrobot.resources.rotation import Rotation


class ArticulatedArm(_BaseArm):
  """An arm with full 3D rotation capability. E.g. a 6-axis robot arm."""

  def __init__(self, backend: ArticulatedGripperArmBackend, reference_resource: Resource):
    super().__init__(backend=backend, reference_resource=reference_resource)
    self.backend: ArticulatedGripperArmBackend = backend  # type: ignore[assignment]

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    await self.backend.open_gripper(gripper_width=gripper_width, backend_params=backend_params)

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    await self.backend.close_gripper(gripper_width=gripper_width, backend_params=backend_params)

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    return await self.backend.is_gripper_closed(backend_params=backend_params)

  @staticmethod
  def _resource_width_for_rotation(resource: Resource, rotation: Rotation) -> float:
    if rotation.z % 180 == 0:
      return resource.get_absolute_size_x()
    else:
      return resource.get_absolute_size_y()

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    rotation: Rotation = Rotation(),
    backend_params: Optional[BackendParams] = None,
  ):
    if self.holding:
      name = self._picked_up.resource.name if self._picked_up else ""
      raise RuntimeError(f"Already holding{' ' + name if name else ''}")
    await self.backend.pick_up_at_location(
      location=location,
      rotation=rotation,
      resource_width=resource_width,
      backend_params=backend_params,
    )
    self._holding_resource_width = resource_width

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    rotation: Rotation = Rotation(),
    backend_params: Optional[BackendParams] = None,
  ):
    location, pickup_distance_from_top = self._prepare_pickup(
      resource, offset, pickup_distance_from_top
    )
    resource_width = self._resource_width_for_rotation(resource, rotation)
    await self.pick_up_at_location(location, resource_width, rotation, backend_params)
    self._picked_up = _PickedUpState(
      resource=resource,
      offset=offset,
      pickup_distance_from_top=pickup_distance_from_top,
      resource_width=resource_width,
      rotation=rotation,
    )
    self._state_updated()

  async def drop_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    backend_params: Optional[BackendParams] = None,
  ):
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    await self.backend.drop_at_location(
      location=location,
      rotation=rotation,
      resource_width=self._holding_resource_width,
      backend_params=backend_params,
    )
    self._end_holding()

  async def drop_resource(
    self,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    rotation: Rotation = Rotation(),
    backend_params: Optional[BackendParams] = None,
  ):
    resource = self._prepare_drop(destination)
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    drop_z = rotation.z
    rotation_applied_by_move = (drop_z - self._picked_up.rotation.z) % 360
    location, final_rotation = self._compute_drop(
      resource=resource,
      destination=destination,
      offset=offset,
      pickup_distance_from_top=self._picked_up.pickup_distance_from_top,
      rotation_applied_by_move=rotation_applied_by_move,
    )
    await self.drop_at_location(location, rotation, backend_params)
    self._finalize_drop(resource, destination, final_rotation)

  async def move_to_location(
    self,
    location: Coordinate,
    rotation: Rotation = Rotation(),
    backend_params: Optional[BackendParams] = None,
  ):
    await self.backend.move_to_location(
      location=location,
      rotation=rotation,
      backend_params=backend_params,
    )

  async def move_picked_up_resource(
    self,
    to: Coordinate,
    rotation: Rotation,
    offset: Coordinate = Coordinate.zero(),
    backend_params: Optional[BackendParams] = None,
  ):
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    location = self._move_location(
      self._picked_up.resource, to, offset, self._picked_up.pickup_distance_from_top
    )
    await self.backend.move_to_location(
      location=location, rotation=rotation, backend_params=backend_params
    )

  async def move_resource(
    self,
    resource: Resource,
    to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    pickup_rotation: Rotation = Rotation(),
    drop_rotation: Rotation = Rotation(),
    pickup_backend_params: Optional[BackendParams] = None,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    await self.pick_up_resource(
      resource=resource,
      offset=pickup_offset,
      pickup_distance_from_top=pickup_distance_from_top,
      rotation=pickup_rotation,
      backend_params=pickup_backend_params,
    )
    for loc in intermediate_locations or []:
      await self.move_picked_up_resource(to=loc, rotation=drop_rotation)
    await self.drop_resource(
      destination=to,
      offset=destination_offset,
      rotation=drop_rotation,
      backend_params=drop_backend_params,
    )
