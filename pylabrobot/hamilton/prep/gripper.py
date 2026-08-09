"""Hamilton Prep CoRe gripper and PrepGripperArm frontend helper."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, Optional

from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_state import place_resource

from . import prep_commands as PrepCmd

if TYPE_CHECKING:
  from .channels import PrepChannels
  from .client import PrepClient

logger = logging.getLogger(__name__)


class PrepGripper:
  """CoRe gripper for Prep — translates plate/tool ops to PrepCmd firmware commands.

  Tool management (pick_up_tool / drop_tool) is handled by the
  :meth:`Prep.core_grippers` context manager.
  """

  def __init__(self, *, client: "PrepClient", channels: "PrepChannels") -> None:
    self._client = client
    self._channels = channels

  @property
  def client(self) -> "PrepClient":
    return self._client

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    *,
    resource_length: float,
    resource_height: float,
    plate_top_z_offset: float,
    clearance_y: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
  ) -> None:
    """Pick up a plate at the specified location.

    Args:
      location: Plate center at grip height (x, y, grip_z) in deck coordinates.
      resource_width: Plate width along the grip axis (Y) in mm.
      resource_length: Plate length (X) in mm.
      resource_height: Plate height (Z) in mm.
      plate_top_z_offset: Offset from grip Z to plate top center Z.
      clearance_y: Approach clearance along the grip axis (mm).
      grip_speed_y: Grip speed (mm/s).
      squeeze_mm: Additional squeeze distance beyond clearance (mm).
    """
    plate_top_center = PrepCmd.XYZCoord(
      default_values=False,
      x_position=location.x,
      y_position=location.y,
      z_position=location.z + plate_top_z_offset,
    )
    plate_dims = PrepCmd.PlateDimensions(
      default_values=False,
      length=resource_length,
      width=resource_width,
      height=resource_height,
    )
    grip_distance = clearance_y + squeeze_mm

    await self._client.send_command(
      PrepCmd.PrepPickUpPlate(
        plate_top_center=plate_top_center,
        plate=plate_dims,
        clearance_y=clearance_y,
        grip_speed_y=grip_speed_y,
        grip_distance=grip_distance,
        grip_height=location.z,
      )
    )

  async def drop_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
  ) -> None:
    """Drop a plate at the specified location.

    Args:
      location: Plate center at place height in deck coordinates.
      resource_width: Plate width along the grip axis (Y) in mm (unused by firmware).
      clearance_y: Release clearance along the grip axis (mm).
      acceleration_scale_x: X-axis acceleration scale.
    """
    del resource_width
    plate_top_center = PrepCmd.XYZCoord(
      default_values=False,
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
    )
    await self._client.send_command(
      PrepCmd.PrepDropPlate(
        plate_top_center=plate_top_center,
        clearance_y=clearance_y,
        acceleration_scale_x=acceleration_scale_x,
      )
    )

  async def move_to_location(
    self,
    location: Coordinate,
    *,
    acceleration_scale_x: int = 1,
  ) -> None:
    """Move a held plate to a new position without releasing it.

    Args:
      location: Target plate center position in deck coordinates.
      acceleration_scale_x: X-axis acceleration scale.
    """
    plate_top_center = PrepCmd.XYZCoord(
      default_values=False,
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
    )
    await self._client.send_command(
      PrepCmd.PrepMovePlate(
        plate_top_center=plate_top_center,
        acceleration_scale_x=acceleration_scale_x,
      )
    )

  async def release_plate(self) -> None:
    """Open the CoRe gripper and release whatever is held (PrepReleasePlate, cmd=21)."""
    await self._client.send_command(PrepCmd.PrepReleasePlate())

  async def pick_up_tool(
    self,
    tool_position_x: float,
    tool_position_z: float,
    front_channel_position_y: float,
    rear_channel_position_y: float,
    *,
    tool_seek: Optional[float] = None,
    tool_x_radius: float = 2.0,
    tool_y_radius: float = 2.0,
    tip_definition: Optional[PrepCmd.TipPickupParameters] = None,
    pre_position: bool = True,
  ) -> None:
    """Pick up CoRe gripper tool (PrepPickUpTool, cmd=15).

    When ``pre_position`` is True (default), moves both channels to the tool XY at
    traverse height before the firmware pickup (same pattern as tip pickup).
    After pickup, moves channels to safe Z.
    """
    if tool_seek is None:
      tool_seek = tool_position_z + 10.0
    if tip_definition is None:
      tip_definition = PrepCmd.CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS
    if pre_position:
      traverse_h = self._channels._resolve_traverse_height()
      await self._channels.move_to_position(
        x=tool_position_x,
        y=[rear_channel_position_y, front_channel_position_y],
        z=traverse_h,
        use_channels=[0, 1],
      )
    await self._client.send_command(
      PrepCmd.PrepPickUpTool(
        tip_definition=tip_definition,
        tool_position_x=tool_position_x,
        tool_position_z=tool_position_z,
        front_channel_position_y=front_channel_position_y,
        rear_channel_position_y=rear_channel_position_y,
        tool_seek=tool_seek,
        tool_x_radius=tool_x_radius,
        tool_y_radius=tool_y_radius,
      )
    )
    await self._channels.move_channels_to_safe_z()

  async def drop_tool(self, *, move_to_safe_z_first: bool = True) -> None:
    """Drop CoRe gripper tool (PrepDropTool, cmd=16)."""
    if move_to_safe_z_first:
      await self._channels.move_channels_to_safe_z()
    await self._client.send_command(PrepCmd.PrepDropTool())


class PrepGripperArm:
  """Resource-aware helper over :class:`PrepGripper` pose commands.

  Resource path: ``pick_up_resource`` / ``drop_resource`` resolve geometry from the
  resource tree (with optional ``offset``) and reassign the held resource on drop.

  Coordinate path: ``pick_up_at_location`` / ``drop_at_location`` take explicit deck
  coordinates (escape hatch for taught points). Prep has no grip-force field;
  squeeze is controlled via ``clearance_y``, ``squeeze_mm``, and ``grip_speed_y``.
  """

  def __init__(
    self,
    backend: PrepGripper,
    reference_resource: Resource,
    grip_axis: Literal["x", "y"] = "y",
  ) -> None:
    self.backend = backend
    self._reference_resource = reference_resource
    self._grip_axis = grip_axis
    self._pickup_distance_from_bottom: Optional[float] = None
    self._holding_resource_width: Optional[float] = None
    self._held_resource: Optional[Resource] = None

  def _resolve_pickup_distance(
    self, resource: Resource, pickup_distance_from_bottom: Optional[float]
  ) -> float:
    if pickup_distance_from_bottom is not None:
      return pickup_distance_from_bottom
    if resource.preferred_pickup_location is not None:
      logger.debug(
        "Using preferred pickup location for resource %s as pickup_distance_from_bottom was "
        "not specified.",
        resource.name,
      )
      return resource.preferred_pickup_location.z
    logger.debug(
      "No preferred pickup location for resource %s. Using default pickup distance of 5mm "
      "from top (= size_z - 5).",
      resource.name,
    )
    return resource.get_size_z() - 5.0

  def _pickup_location(
    self,
    resource: Resource,
    offset: Coordinate,
    pickup_distance_from_bottom: float,
  ) -> Coordinate:
    center = resource.center().rotated(resource.get_absolute_rotation())
    if resource.is_in_subtree_of(self._reference_resource):
      loc = resource.get_location_wrt(self._reference_resource, "l", "f", "b") + center + offset
    else:
      loc = center + offset
    return Coordinate(loc.x, loc.y, loc.z + pickup_distance_from_bottom)

  def _drop_location(self, destination: Resource, offset: Coordinate) -> Coordinate:
    if self._held_resource is None or self._pickup_distance_from_bottom is None:
      raise RuntimeError(
        "drop_resource requires a prior pick_up_resource (held resource and grip height)."
      )
    held = self._held_resource
    pdfb = self._pickup_distance_from_bottom
    if isinstance(destination, ResourceHolder):
      child = destination.get_default_child_location(held)
    else:
      child = Coordinate.zero()
    center = held.center().rotated(held.get_absolute_rotation())
    plate_lfb = destination.get_location_wrt(self._reference_resource, "l", "f", "b") + child
    loc = plate_lfb + center + offset
    return Coordinate(loc.x, loc.y, loc.z + pdfb)

  def _resource_width(self, resource: Resource) -> float:
    if self._grip_axis == "y":
      return resource.get_absolute_size_y()
    return resource.get_absolute_size_x()

  def _clear_held_state(self) -> None:
    self._holding_resource_width = None
    self._pickup_distance_from_bottom = None
    self._held_resource = None

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_bottom: Optional[float] = None,
    *,
    resource_width: Optional[float] = None,
    resource_length: Optional[float] = None,
    resource_height: Optional[float] = None,
    plate_top_z_offset: Optional[float] = None,
    clearance_y: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
  ) -> None:
    pdfb = self._resolve_pickup_distance(resource, pickup_distance_from_bottom)
    if resource_width is None:
      resource_width = self._resource_width(resource)
    if resource_length is None:
      resource_length = resource.get_absolute_size_x()
    if resource_height is None:
      resource_height = resource.get_absolute_size_z()
    if plate_top_z_offset is None:
      plate_top_z_offset = resource.get_absolute_size_z() - pdfb

    location = self._pickup_location(resource, offset, pdfb)
    await self.backend.pick_up_at_location(
      location,
      resource_width,
      resource_length=resource_length,
      resource_height=resource_height,
      plate_top_z_offset=plate_top_z_offset,
      clearance_y=clearance_y,
      grip_speed_y=grip_speed_y,
      squeeze_mm=squeeze_mm,
    )
    self._pickup_distance_from_bottom = pdfb
    self._holding_resource_width = resource_width
    self._held_resource = resource

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    *,
    resource_length: float,
    resource_height: float,
    plate_top_z_offset: float,
    clearance_y: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
  ) -> None:
    """Pick up at an explicit grip-point coordinate (no resource-tree geometry).

    Sets held width so ``drop_at_location`` works. Does not set a held
    :class:`Resource`; use ``drop_resource`` only after ``pick_up_resource``.
    """
    await self.backend.pick_up_at_location(
      location,
      resource_width,
      resource_length=resource_length,
      resource_height=resource_height,
      plate_top_z_offset=plate_top_z_offset,
      clearance_y=clearance_y,
      grip_speed_y=grip_speed_y,
      squeeze_mm=squeeze_mm,
    )
    self._holding_resource_width = resource_width
    self._pickup_distance_from_bottom = None
    self._held_resource = None

  async def drop_resource(
    self,
    destination: Resource,
    offset: Coordinate = Coordinate.zero(),
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
  ) -> None:
    """Drop the held resource onto a destination resource (e.g. a PrepDeck spot).

    Resolves place geometry from the destination holder + held plate, then
    reassigns the resource tree after a successful firmware drop.
    """
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    if self._held_resource is None or self._pickup_distance_from_bottom is None:
      raise RuntimeError(
        "drop_resource requires a prior pick_up_resource (held resource and grip height)."
      )
    held = self._held_resource
    destination.check_can_drop_resource_here(held)
    location = self._drop_location(destination, offset)
    await self.backend.drop_at_location(
      location,
      self._holding_resource_width,
      clearance_y=clearance_y,
      acceleration_scale_x=acceleration_scale_x,
    )
    self._clear_held_state()
    place_resource(held, destination)

  async def drop_at_location(
    self,
    location: Coordinate,
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
  ) -> None:
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    await self.backend.drop_at_location(
      location,
      self._holding_resource_width,
      clearance_y=clearance_y,
      acceleration_scale_x=acceleration_scale_x,
    )
    self._clear_held_state()

  async def move_to_location(
    self,
    location: Coordinate,
    *,
    acceleration_scale_x: int = 1,
  ) -> None:
    await self.backend.move_to_location(location, acceleration_scale_x=acceleration_scale_x)
