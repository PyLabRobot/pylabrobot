import logging
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple, Union

from pylabrobot.arms.backend import GripperArmBackend, _BaseArmBackend
from pylabrobot.arms.standard import GripDirection, GripperLocation
from pylabrobot.capabilities.capability import BackendParams, Capability
from pylabrobot.legacy.tilting.tilter import Tilter
from pylabrobot.resources import (
  Coordinate,
  Lid,
  Plate,
  PlateAdapter,
  Resource,
  ResourceHolder,
  ResourceStack,
  Trash,
)
from pylabrobot.resources.rotation import Rotation

logger = logging.getLogger(__name__)

GripOrientation = Union[GripDirection, float]


@dataclass
class _PickedUpState:
  resource: Resource
  offset: Coordinate
  pickup_distance_from_top: float
  resource_width: float
  rotation: Rotation = Rotation()


class _BaseArm(Capability):
  """Base class for all arm types. Not instantiated directly."""

  def __init__(self, backend, reference_resource: Resource):
    super().__init__(backend=backend)
    self.backend: _BaseArmBackend = backend
    self._reference_resource = reference_resource
    self._picked_up: Optional[_PickedUpState] = None
    self._holding_resource_width: Optional[float] = None

  async def _on_setup(self, **backend_kwargs):
    await super()._on_setup(**backend_kwargs)
    self._picked_up = None
    self._holding_resource_width = None

  async def _on_stop(self):
    await super()._on_stop()
    self._picked_up = None
    self._holding_resource_width = None

  def _state_updated(self):
    pass

  @property
  def holding(self) -> bool:
    return self._holding_resource_width is not None

  def get_picked_up_resource(self) -> Optional[Resource]:
    if self._picked_up is not None:
      return self._picked_up.resource
    return None

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    """Stop any ongoing movement of the arm."""
    return await self.backend.halt(backend_params=backend_params)

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Park the arm to its default position."""
    return await self.backend.park(backend_params=backend_params)

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    """Get the current location and rotation of the gripper."""
    return await self.backend.request_gripper_location(backend_params=backend_params)

  # -- holding state -----------------------------------------------------------

  def _begin_holding(self, resource_width: float):
    if self.holding:
      name = self._picked_up.resource.name if self._picked_up else ""
      raise RuntimeError(f"Already holding{' ' + name if name else ''}")
    self._holding_resource_width = resource_width

  def _end_holding(self):
    self._picked_up = None
    self._holding_resource_width = None

  # -- coordinate computation -------------------------------------------------

  def _pickup_location(
    self,
    resource: Resource,
    offset: Coordinate,
    pickup_distance_from_top: float,
  ) -> Coordinate:
    assert self._reference_resource is not None
    center = resource.center().rotated(resource.get_absolute_rotation())
    if resource.is_in_subtree_of(self._reference_resource):
      loc = resource.get_location_wrt(self._reference_resource, "l", "f", "t") + center + offset
    else:
      loc = center + offset
    return Coordinate(loc.x, loc.y, loc.z - pickup_distance_from_top)

  def _destination_location(
    self,
    resource: Resource,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    resource_rotation_wrt_destination_wrt_local: float,
  ) -> Coordinate:
    assert self._reference_resource is not None
    if isinstance(destination, ResourceStack):
      assert destination.direction == "z"
      return destination.get_location_wrt(
        self._reference_resource
      ) + destination.get_new_child_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
    elif isinstance(destination, Coordinate):
      return destination
    elif isinstance(destination, ResourceHolder):
      if destination.resource is not None and destination.resource is not resource:
        raise RuntimeError("Destination already has a plate")
      child_wrt_parent = destination.get_default_child_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
      return destination.get_location_wrt(self._reference_resource) + child_wrt_parent
    elif isinstance(destination, PlateAdapter):
      if not isinstance(resource, Plate):
        raise ValueError("Only plates can be moved to a PlateAdapter")
      adjusted_plate_anchor = destination.compute_plate_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
      return destination.get_location_wrt(self._reference_resource) + adjusted_plate_anchor
    elif isinstance(destination, Plate) and isinstance(resource, Lid):
      plate_location = destination.get_location_wrt(self._reference_resource)
      child_wrt_parent = destination.get_lid_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
      return plate_location + child_wrt_parent
    else:
      return destination.get_location_wrt(self._reference_resource)

  def _compute_end_effector_location(
    self,
    resource: Resource,
    to_location: Coordinate,
    offset: Coordinate,
    pickup_distance_from_top: float,
    rotation_applied_by_move: float,
  ) -> Coordinate:
    center = resource.center().rotated(
      Rotation(z=resource.get_absolute_rotation().z + rotation_applied_by_move)
    )
    loc = to_location + center + offset
    return Coordinate(
      loc.x,
      loc.y,
      loc.z + resource.get_absolute_size_z() - pickup_distance_from_top,
    )

  def _move_location(
    self,
    resource: Resource,
    to: Coordinate,
    offset: Coordinate,
    pickup_distance_from_top: float,
  ) -> Coordinate:
    return to + resource.get_anchor("c", "c", "t") - Coordinate(z=pickup_distance_from_top) + offset

  def _resolve_pickup_distance(
    self, resource: Resource, pickup_distance_from_top: Optional[float]
  ) -> float:
    if pickup_distance_from_top is not None:
      return pickup_distance_from_top
    if resource.preferred_pickup_location is not None:
      logger.debug(
        "Using preferred pickup location for resource %s as pickup_distance_from_top was "
        "not specified.",
        resource.name,
      )
      return resource.get_size_z() - resource.preferred_pickup_location.z
    logger.debug(
      "No preferred pickup location for resource %s. Using default pickup distance of 5mm.",
      resource.name,
    )
    return 5.0

  def _assign_after_drop(
    self,
    resource: Resource,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
  ) -> None:
    assert self._reference_resource is not None
    resource.unassign()
    if isinstance(destination, Coordinate):
      destination -= self._reference_resource.location
      self._reference_resource.assign_child_resource(resource, location=destination)
    elif isinstance(destination, ResourceHolder):
      destination.assign_child_resource(resource)
    elif isinstance(destination, ResourceStack):
      if destination.direction != "z":
        raise ValueError("Only ResourceStacks with direction 'z' are currently supported")
      destination.assign_child_resource(resource)
    elif isinstance(destination, Tilter):
      destination.assign_child_resource(resource, location=destination.child_location)
    elif isinstance(destination, PlateAdapter):
      if not isinstance(resource, Plate):
        raise ValueError("Only plates can be moved to a PlateAdapter")
      destination.assign_child_resource(
        resource, location=destination.compute_plate_location(resource)
      )
    elif isinstance(destination, Plate) and isinstance(resource, Lid):
      destination.assign_child_resource(resource)
    elif isinstance(destination, Trash):
      pass
    else:
      destination.assign_child_resource(
        resource, location=destination.get_location_wrt(self._reference_resource)
      )

  def _compute_drop(
    self,
    resource: Resource,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    offset: Coordinate,
    pickup_distance_from_top: float,
    rotation_applied_by_move: float = 0,
  ) -> Tuple[Coordinate, float]:
    resource_absolute_rotation_after_move = (
      resource.get_absolute_rotation().z + rotation_applied_by_move
    )
    dest_rotation = (
      destination.get_absolute_rotation().z if not isinstance(destination, Coordinate) else 0
    )
    resource_rotation_wrt_destination = resource_absolute_rotation_after_move - dest_rotation
    resource_rotation_wrt_destination_wrt_local = (
      resource_rotation_wrt_destination - resource.rotation.z
    )

    if isinstance(destination, ResourceStack):
      if resource_rotation_wrt_destination % 180 != 0:
        raise ValueError(
          "Resource rotation wrt ResourceStack must be a multiple of 180 degrees, "
          f"got {resource_rotation_wrt_destination} degrees"
        )

    to_location = self._destination_location(
      resource, destination, resource_rotation_wrt_destination_wrt_local
    )
    location = self._compute_end_effector_location(
      resource, to_location, offset, pickup_distance_from_top, rotation_applied_by_move
    )
    return location, resource_rotation_wrt_destination

  def _prepare_pickup(
    self,
    resource: Resource,
    offset: Coordinate,
    pickup_distance_from_top: Optional[float],
  ) -> Tuple[Coordinate, float]:
    pickup_distance_from_top = self._resolve_pickup_distance(resource, pickup_distance_from_top)
    assert resource.get_absolute_rotation().x == 0 and resource.get_absolute_rotation().y == 0
    assert resource.get_absolute_rotation().z % 90 == 0
    location = self._pickup_location(resource, offset, pickup_distance_from_top)
    return location, pickup_distance_from_top

  def _prepare_drop(
    self,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
  ) -> Resource:
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    if isinstance(destination, Resource):
      destination.check_can_drop_resource_here(self._picked_up.resource)
    return self._picked_up.resource

  def _finalize_drop(
    self,
    resource: Resource,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    resource_rotation_wrt_destination: float,
  ) -> None:
    self._end_holding()
    self._state_updated()
    resource.rotate(z=resource_rotation_wrt_destination - resource.rotation.z)
    self._assign_after_drop(resource, destination)


class GripperArm(_BaseArm):
  """A gripper arm without rotation capability. E.g. Hamilton core grippers."""

  def __init__(
    self,
    backend: GripperArmBackend,
    reference_resource: Resource,
    grip_axis: Literal["x", "y"] = "x",
  ):
    super().__init__(backend=backend, reference_resource=reference_resource)
    self.backend: GripperArmBackend = backend
    self._grip_axis = grip_axis

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    return await self.backend.open_gripper(
      gripper_width=gripper_width, backend_params=backend_params
    )

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    return await self.backend.close_gripper(
      gripper_width=gripper_width, backend_params=backend_params
    )

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    return await self.backend.is_gripper_closed(backend_params=backend_params)

  def _resource_width(self, resource: Resource) -> float:
    if self._grip_axis == "y":
      return resource.get_absolute_size_y()
    return resource.get_absolute_size_x()

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ):
    self._begin_holding(resource_width)
    await self.backend.pick_up_at_location(
      location=location, resource_width=resource_width, backend_params=backend_params
    )

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ):
    location, pickup_distance_from_top = self._prepare_pickup(
      resource, offset, pickup_distance_from_top
    )
    resource_width = self._resource_width(resource)
    await self.pick_up_at_location(location, resource_width, backend_params)
    self._picked_up = _PickedUpState(
      resource=resource,
      offset=offset,
      pickup_distance_from_top=pickup_distance_from_top,
      resource_width=resource_width,
    )
    self._state_updated()

  async def drop_at_location(
    self,
    location: Coordinate,
    backend_params: Optional[BackendParams] = None,
  ):
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    await self.backend.drop_at_location(
      location=location, resource_width=self._holding_resource_width, backend_params=backend_params
    )
    self._end_holding()

  async def drop_resource(
    self,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    backend_params: Optional[BackendParams] = None,
  ):
    resource = self._prepare_drop(destination)
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    location, rotation = self._compute_drop(
      resource=resource,
      destination=destination,
      offset=offset,
      pickup_distance_from_top=self._picked_up.pickup_distance_from_top,
    )
    await self.drop_at_location(location, backend_params)
    self._finalize_drop(resource, destination, rotation)

  async def move_to_location(
    self, location: Coordinate, backend_params: Optional[BackendParams] = None
  ):
    await self.backend.move_to_location(location=location, backend_params=backend_params)

  async def move_picked_up_resource(
    self,
    to: Coordinate,
    offset: Coordinate = Coordinate.zero(),
    backend_params: Optional[BackendParams] = None,
  ):
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    location = self._move_location(
      self._picked_up.resource, to, offset, self._picked_up.pickup_distance_from_top
    )
    await self.backend.move_to_location(location=location, backend_params=backend_params)

  async def move_resource(
    self,
    resource: Resource,
    to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    pickup_backend_params: Optional[BackendParams] = None,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    await self.pick_up_resource(
      resource=resource,
      offset=pickup_offset,
      pickup_distance_from_top=pickup_distance_from_top,
      backend_params=pickup_backend_params,
    )
    for loc in intermediate_locations or []:
      await self.move_picked_up_resource(to=loc)
    await self.drop_resource(
      destination=to, offset=destination_offset, backend_params=drop_backend_params
    )
