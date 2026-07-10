from typing import Dict, List, Optional, Union

from pylabrobot.capabilities.arms.arm import GripperArm, GripperOrientation, _PickedUpState
from pylabrobot.capabilities.arms.backend import OrientableGripperArmBackend
from pylabrobot.capabilities.arms.standard import (
  _GRIPPER_DIRECTION_VALUES,
  GripperDirection,
)
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Resource, ResourceHolder, ResourceStack
from pylabrobot.resources.rotation import Rotation

# Cardinal-direction → degrees under the standard ``rotation.z = 0 → +X``
# convention (CCW about world +Z, right-hand rule looking down). In the
# PLR deck frame (+X = right, +Y = back), this maps to: right=+X,
# back=+Y, left=-X, front=-Y. ``front`` lives at 270° (rather than -90°)
# so the table reads top-to-bottom in CCW order from +X.
_GRIPPER_DIRECTION_TO_DEGREES: Dict[GripperDirection, float] = {
  "right": 0.0,
  "back": 90.0,
  "left": 180.0,
  "front": 270.0,
}


def _resolve_direction(direction: GripperOrientation) -> float:
  if isinstance(direction, str):
    if direction not in _GRIPPER_DIRECTION_VALUES:
      raise ValueError(
        f"direction must be one of {sorted(_GRIPPER_DIRECTION_VALUES)} or a float, "
        f"got {direction!r}"
      )
    return _GRIPPER_DIRECTION_TO_DEGREES[direction]
  return direction


class OrientableGripperArm(GripperArm):
  """A gripper arm with rotation capability. E.g. Hamilton iSWAP."""

  def __init__(self, backend: OrientableGripperArmBackend, reference_resource: Resource):
    super().__init__(backend=backend, reference_resource=reference_resource)  # type: ignore[arg-type]
    self.backend: OrientableGripperArmBackend = backend  # type: ignore[assignment]

  @staticmethod
  def _resource_width_for_direction(resource: Resource, direction: float) -> float:
    # Front-finger axis is `direction`; jaws come together perpendicular to
    # it. At 0°/180° the finger points along ±X (right/left), so the jaws
    # grip the resource along Y; at 90°/270° the finger points along ±Y
    # (back/front) so they grip along X.
    # TODO: resource rotation is not taken into account here.
    if direction % 180 == 0:
      return resource.get_absolute_size_y()
    else:
      return resource.get_absolute_size_x()

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    direction: GripperOrientation = 0.0,
    backend_params: Optional[BackendParams] = None,
  ):
    if self.holding:
      name = self._picked_up.resource.name if self._picked_up else ""
      raise RuntimeError(f"Already holding{' ' + name if name else ''}")
    dir_degrees = _resolve_direction(direction)
    await self.backend.pick_up_at_location(
      location=location,
      direction=dir_degrees,
      resource_width=resource_width,
      backend_params=backend_params,
    )
    self._holding_resource_width = resource_width

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_bottom: Optional[float] = None,
    direction: GripperOrientation = "front",
    backend_params: Optional[BackendParams] = None,
  ):
    location, pickup_distance_from_bottom = self._prepare_pickup(
      resource, offset, pickup_distance_from_bottom
    )
    dir_degrees = _resolve_direction(direction)
    resource_width = self._resource_width_for_direction(resource, dir_degrees)
    resource_absolute_rotation_at_pickup = resource.get_absolute_rotation()
    await self.pick_up_at_location(location, resource_width, dir_degrees, backend_params)
    self._picked_up = _PickedUpState(
      resource=resource,
      offset=offset,
      pickup_distance_from_bottom=pickup_distance_from_bottom,
      resource_width=resource_width,
      resource_absolute_rotation_at_pickup=resource_absolute_rotation_at_pickup,
      rotation=Rotation(z=dir_degrees),
    )
    resource.unassign()
    self._state_updated()

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: GripperOrientation,
    backend_params: Optional[BackendParams] = None,
  ):
    if self._holding_resource_width is None:
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
    direction: GripperOrientation = "front",
    backend_params: Optional[BackendParams] = None,
  ):
    resource = self._prepare_drop(destination)
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    drop_dir = _resolve_direction(direction)
    rotation_applied_by_move = (drop_dir - self._picked_up.rotation.z) % 360
    location, rotation = self._compute_drop(
      resource=resource,
      destination=destination,
      offset=offset,
      pickup_distance_from_bottom=self._picked_up.pickup_distance_from_bottom,
      rotation_applied_by_move=rotation_applied_by_move,
      resource_absolute_rotation_at_pickup=(
        self._picked_up.resource_absolute_rotation_at_pickup
      ),
    )
    await self.drop_at_location(location, drop_dir, backend_params)
    self._finalize_drop(resource, destination, rotation)

  async def move_to_location(
    self,
    location: Coordinate,
    direction: GripperOrientation = 0.0,
    backend_params: Optional[BackendParams] = None,
  ):
    await self.backend.move_to_location(
      location=location,
      direction=_resolve_direction(direction),
      backend_params=backend_params,
    )

  async def move_picked_up_resource(
    self,
    to: Coordinate,
    direction: GripperOrientation,
    offset: Coordinate = Coordinate.zero(),
    backend_params: Optional[BackendParams] = None,
  ):
    if self._picked_up is None:
      raise RuntimeError("No resource picked up")
    dir_degrees = _resolve_direction(direction)
    location = self._move_location(
      self._picked_up.resource, to, offset, self._picked_up.pickup_distance_from_bottom
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
    pickup_distance_from_bottom: Optional[float] = None,
    pickup_direction: GripperOrientation = "front",
    drop_direction: GripperOrientation = "front",
    pickup_backend_params: Optional[BackendParams] = None,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    await self.pick_up_resource(
      resource=resource,
      offset=pickup_offset,
      pickup_distance_from_bottom=pickup_distance_from_bottom,
      direction=pickup_direction,
      backend_params=pickup_backend_params,
    )
    for loc in intermediate_locations or []:
      await self.move_picked_up_resource(to=loc, direction=drop_direction)
    await self.drop_resource(
      destination=to,
      offset=destination_offset,
      direction=drop_direction,
      backend_params=drop_backend_params,
    )
