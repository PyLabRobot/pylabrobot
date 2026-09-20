"""The Hamilton Prep's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Dict, List, Literal, Optional, Tuple

from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_state import place_resource

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver
  from .pipettes import Pipettes
  from .x_arm import XArm

logger = logging.getLogger(__name__)


class CoreGrippers:
  """The CoRe grippers: the tools they grip with, and what they take.

  ``pick_up_tools`` / ``return_tools`` mount the paddles, which nothing is gripped without.
  ``pick_up_resource`` / ``drop_resource`` resolve geometry from the resource tree;
  ``pick_up_at_location`` / ``drop_at_location`` are told the point instead.

  Prep has no grip-force field: ``clearance_y``, ``squeeze_mm`` and ``grip_speed_y`` set how hard
  the jaws close.
  """

  def __init__(
    self,
    driver: "PrepDriver",
    grip_axis: Literal["x", "y"] = "y",
  ) -> None:
    """
    Args:
      driver: the driver to send commands through, whose pipettes carry the grippers and whose
        deck the resource calls are measured against.
      grip_axis: which way the tools close on what they take.
    """
    self._driver = driver
    self._grip_axis = grip_axis
    self._pickup_distance_from_bottom: Optional[float] = None
    self._holding_resource_width: Optional[float] = None
    self._held_resource: Optional[Resource] = None
    self._tools_mounted = False
    self._parked_tools: List[Tuple[HeadTool, Optional[Resource], Optional[Coordinate]]] = []

  # -- channels and deck ---------------------------------------------------------------------------

  # -- what carries them ---------------------------------------------------------------------------

  @property
  def _pipettes(self) -> "Pipettes":
    """The pipetting channels that carry the grippers.

    Raises:
      RuntimeError: If the driver has no pipettes yet.
    """
    if self._driver.pipettes is None:
      raise RuntimeError("no pipettes to carry the grippers; have you called `prep.setup()`?")
    return self._driver.pipettes

  @property
  def _deck(self) -> Deck:
    """The deck positions are measured from, as the driver has it now."""
    return self._driver.deck

  # Tools
  # -- mounting ------------------------------------------------------------------------------------

  @property
  def _x_arm(self) -> "XArm":
    """The arm the channels holding the tools ride.

    Raises:
      RuntimeError: If the driver has no x-arm yet.
    """
    if self._driver.x_arm is None:
      raise RuntimeError("no x-arm to carry the grippers; have you called `prep.setup()`?")
    return self._driver.x_arm

  # -- state ---------------------------------------------------------------------------------------

  @property
  def tools_mounted(self) -> bool:
    """Whether the tools are on the channels."""
    return self._tools_mounted

  async def request_tool_attached(self, channel: int) -> bool:
    """Whether the device says a tool is on `channel`.

    Read from the machine rather than from what this session did: the firmware keeps what it holds
    across a restart, and :attr:`tools_mounted` does not.

    Args:
      channel: which pipette, 0-indexed from the back.

    Returns:
      True when the channel carries something the firmware calls a tool.

    Raises:
      ValueError: If the channel does not exist.
    """
    attached = await self._pipettes.request_attached_tip_information(channel)
    return attached is not None and bool(attached.is_tool)

  async def _raise_to_traverse(self, minimum_traverse_height_end: Optional[float]) -> None:
    """Leave the channels at a height they can travel at, as every other move here does.

    Args:
      minimum_traverse_height_end: where to leave them, in mm. None is the configured default.
    """
    height = self._pipettes._resolve_traverse_height(minimum_traverse_height_end)
    await self._pipettes.move_tool_bottom_to_z_positions(
      {channel: height for channel in range(self._pipettes.num_channels)}
    )

  def _require_mounted(self) -> None:
    """Raise unless the tools are on the channels.

    The grippers are there from setup; the tools are not, and nothing can be taken until they have
    been picked up. This used to be said by the object not existing yet - it is said here instead,
    now that there is one feature reachable either way.
    """
    if not self._tools_mounted:
      raise RuntimeError(
        "the CoRe gripper tools are not mounted. Call "
        "`await prep.core_grippers.pick_up_tools()` first, or use "
        "`async with prep.core_grippers.mounted():`."
      )

  # -- firmware ------------------------------------------------------------------------------------

  def _clear_held_state(self) -> None:
    self._holding_resource_width = None
    self._pickup_distance_from_bottom = None
    self._held_resource = None

  # ----------------------------------------
  # Tools
  # ----------------------------------------
  # -- firmware ------------------------------------------------------------------------------------

  async def pick_up_tools_at_location(
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
  ) -> None:
    """Pick up CoRe gripper tool (PrepPickUpTool, cmd=15).

    Both channels travel over the tools at traverse height first, as a tip pick-up does, so the
    pick-up itself is straight down. Afterwards they are moved to safe Z.
    """
    if tool_seek is None:
      tool_seek = tool_position_z + 10.0
    if tip_definition is None:
      tip_definition = PrepCmd.CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS
    # Over the tools first, so the pick-up itself is straight down
    await self._pipettes.move_to_xy_positions(
      tool_position_x,
      {0: rear_channel_position_y, 1: front_channel_position_y},
      minimum_traverse_height_start=self._pipettes._resolve_traverse_height(),
    )
    try:
      await self._driver.send_command(
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
    finally:
      # A pick-up leaves the channels down at the tools, worked or not, and the next lateral move
      # would drag them through the holder.
      await self._pipettes.move_to_safe_z()
      await self._pipettes._record_channel_bounds()

  async def drop_tools(self, *, move_to_safe_z_first: bool = True) -> None:
    """Let go of the tools where they stand (PrepDropTool, cmd=16)."""
    if move_to_safe_z_first:
      await self._pipettes.move_to_safe_z()
    await self._driver.send_command(PrepCmd.PrepDropTool())
    await self._pipettes._record_channel_bounds()

  # Movement
  # -- the gantry the tools ride -------------------------------------------------------------------

  # -- mounting ------------------------------------------------------------------------------------

  def _park_tools(self) -> None:
    """Put the tools back where they were taken from, once the device has let go of them."""
    for tool, holder, location in self._parked_tools:
      if tool.parent is not None:
        tool.parent.unassign_child_resource(tool)
      if holder is not None:
        holder.assign_child_resource(tool, location=location)
    self._parked_tools = []

  async def pick_up_tools(self) -> None:
    """Take the tools out of the holder the deck carries and onto the channels.

    Raises:
      RuntimeError: If they are already mounted, setup has not run, or the channels do not report
        a tool once the pick-up has run.
      TypeError: If the deck carries no holder, or the holder is empty.
    """
    if self._tools_mounted:
      raise RuntimeError("the CoRe gripper tools are already mounted")
    holder = self._driver.core_gripper_holder
    if holder is None:
      raise TypeError("the deck carries no CO-RE gripper holder")
    tools = [child for child in holder.children if isinstance(child, HeadTool)]
    if not tools:
      raise TypeError("the holder carries no CO-RE grip tools to pick up")

    # Into the tools as far as they take a channel: their base is 25 mm below that and their tops
    # 8 mm above it, and a channel stopped at either does not seat the tool.
    deck = self._deck
    loc = holder.get_location_wrt(deck, x="c")
    engage = min(tool.get_location_wrt(deck, z="t").z - tool.fitting_depth for tool in tools)
    await self.pick_up_tools_at_location(
      tool_position_x=loc.x,
      tool_position_z=engage,
      front_channel_position_y=loc.y + holder.front_channel_y_center,
      rear_channel_position_y=loc.y + holder.back_channel_y_center,
      tool_seek=engage + 10.0,
    )

    # A taken tool rides where the channel rides, as a tip does. Channel 0 takes the rear tool.
    self._parked_tools = [(tool, tool.parent, tool.location) for tool in tools]
    rear_first = sorted(tools, key=lambda tool: tool.get_location_wrt(deck, y="c").y)
    taken = []
    for channel, tool in zip((0, 1), reversed(rear_first)):
      shaft = self._pipettes.shaft(channel)
      if shaft is not None:
        shaft.mount_tip(tool)
        taken.append(channel)

    # The command returning is not the tools being on. Asked once per mount, not once per grip: a
    # channel that reports none puts the tools back where they were, so the model never holds what
    # the device does not.
    missing = [channel for channel in taken if not await self.request_tool_attached(channel)]
    if missing:
      self._park_tools()
      raise RuntimeError(
        f"the pick-up ran, but {'channel' if len(missing) == 1 else 'channels'} "
        f"{', '.join(str(c) for c in missing)} report no tool on them. The tools are back in "
        "their holder in the model; check the device before running anything."
      )
    self._tools_mounted = True

  async def return_tools(self) -> None:
    """Put the tools back in their holder, if the device lets go of them.

    A drop that fails leaves the channels holding them, and the model says so.
    """
    if not self._tools_mounted:
      return
    await self.drop_tools()
    self._tools_mounted = False
    self._clear_held_state()
    self._park_tools()

  @asynccontextmanager
  async def mounted(self) -> AsyncIterator["CoreGrippers"]:
    """The tools on for as long as the block runs, and returned on the way out."""
    await self.pick_up_tools()
    try:
      yield self
    finally:
      await self.return_tools()

  # ----------------------------------------
  # Movement
  # ----------------------------------------
  # -- the gantry the tools ride -------------------------------------------------------------------

  async def move_to_x_position(
    self,
    x: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Move the tools along X. See :meth:`XArm.move_to_x_position`."""
    self._require_mounted()
    await self._x_arm.move_to_x_position(
      x,
      speed=speed,
      acceleration=acceleration,
      minimum_traverse_height_start=minimum_traverse_height_start,
      z_speed=z_speed,
      z_acceleration=z_acceleration,
    )

  async def move_to_y_position(self, channel: int, y: float, speed: Optional[float] = None) -> None:
    """Move one tool along Y. See :meth:`Pipettes.move_to_y_position`."""
    self._require_mounted()
    await self._pipettes.move_to_y_position(channel, y, speed=speed)

  async def move_to_xy_positions(
    self,
    x: float,
    ys: Dict[int, float],
    *,
    make_space: bool = False,
    minimum_traverse_height_start: Optional[float] = None,
    via_lane: bool = False,
    x_speed: Optional[float] = None,
    x_speed_scale: Optional[int] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Move the tools across the deck. See :meth:`Pipettes.move_to_xy_positions`."""
    self._require_mounted()
    await self._pipettes.move_to_xy_positions(
      x,
      ys,
      make_space=make_space,
      minimum_traverse_height_start=minimum_traverse_height_start,
      via_lane=via_lane,
      x_speed=x_speed,
      x_speed_scale=x_speed_scale,
      z_speed=z_speed,
      z_acceleration=z_acceleration,
    )

  async def move_to_safe_z(self, channels: Optional[List[int]] = None) -> None:
    """Raise the tools to Z safety. See :meth:`Pipettes.move_to_safe_z`."""
    self._require_mounted()
    await self._pipettes.move_to_safe_z(channels)

  # -- with a plate held ---------------------------------------------------------------------------

  # -- with a plate held ---------------------------------------------------------------------------

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
    await self._driver.send_command(
      PrepCmd.PrepMovePlate(
        plate_top_center=plate_top_center,
        acceleration_scale_x=acceleration_scale_x,
      )
    )

  # Resources
  # -- by resource ---------------------------------------------------------------------------------

  # ----------------------------------------
  # Resources
  # ----------------------------------------
  # -- geometry ------------------------------------------------------------------------------------

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
    if resource.is_in_subtree_of(self._deck):
      loc = resource.get_location_wrt(self._deck, "l", "f", "b") + center + offset
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
    plate_lfb = destination.get_location_wrt(self._deck, "l", "f", "b") + child
    loc = plate_lfb + center + offset
    return Coordinate(loc.x, loc.y, loc.z + pdfb)

  def _resource_width(self, resource: Resource) -> float:
    if self._grip_axis == "y":
      return resource.get_absolute_size_y()
    return resource.get_absolute_size_x()

  # -- at a location -------------------------------------------------------------------------------

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
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Pick up a plate at the specified location.

    Told where to close rather than what to take, so it leaves no held :class:`Resource` behind
    it: it sets the width it gripped at, which is what lets go of it again, and ``drop_resource``
    stays for what ``pick_up_resource`` took.

    Args:
      location: Plate center at grip height (x, y, grip_z) in deck coordinates.
      resource_width: Plate width along the grip axis (Y) in mm.
      resource_length: Plate length (X) in mm.
      resource_height: Plate height (Z) in mm.
      plate_top_z_offset: Offset from grip Z to plate top center Z.
      clearance_y: Approach clearance along the grip axis (mm).
      grip_speed_y: Grip speed (mm/s).
      squeeze_mm: Additional squeeze distance beyond clearance (mm).
      minimum_traverse_height_end: the height to leave the channels at once it is gripped, in mm.
        None is the configured default. The firmware leaves them at the grip height, and the next
        lateral move would drag what they are holding across whatever is between.
    """
    self._require_mounted()
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

    await self._driver.send_command(
      PrepCmd.PrepPickUpPlate(
        plate_top_center=plate_top_center,
        plate=plate_dims,
        clearance_y=clearance_y,
        grip_speed_y=grip_speed_y,
        grip_distance=grip_distance,
        grip_height=location.z,
      )
    )
    await self._raise_to_traverse(minimum_traverse_height_end)
    self._holding_resource_width = resource_width
    self._pickup_distance_from_bottom = None
    self._held_resource = None

  async def drop_at_location(
    self,
    location: Coordinate,
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Drop a plate at the specified location.

    The jaws open by a clearance, so how wide what they are holding is does not come into it.

    Args:
      location: Plate center at place height in deck coordinates.
      clearance_y: Release clearance along the grip axis (mm).
      acceleration_scale_x: X-axis acceleration scale.
      minimum_traverse_height_end: the height to leave the channels at once it is released, in mm.
        None is the configured default.
    """
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    plate_top_center = PrepCmd.XYZCoord(
      default_values=False,
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
    )
    await self._driver.send_command(
      PrepCmd.PrepDropPlate(
        plate_top_center=plate_top_center,
        clearance_y=clearance_y,
        acceleration_scale_x=acceleration_scale_x,
      )
    )
    await self._raise_to_traverse(minimum_traverse_height_end)
    self._clear_held_state()

  async def release_plate(self) -> None:
    """Open the CoRe gripper and release whatever is held (PrepReleasePlate, cmd=21)."""
    await self._driver.send_command(PrepCmd.PrepReleasePlate())

  # -- geometry, and what is held ------------------------------------------------------------------

  # -- by resource ---------------------------------------------------------------------------------

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
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    self._require_mounted()
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
    await self.pick_up_at_location(
      location,
      resource_width,
      resource_length=resource_length,
      resource_height=resource_height,
      plate_top_z_offset=plate_top_z_offset,
      clearance_y=clearance_y,
      grip_speed_y=grip_speed_y,
      squeeze_mm=squeeze_mm,
      minimum_traverse_height_end=minimum_traverse_height_end,
    )
    self._pickup_distance_from_bottom = pdfb
    self._holding_resource_width = resource_width
    self._held_resource = resource

  async def drop_resource(
    self,
    destination: Resource,
    offset: Coordinate = Coordinate.zero(),
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_end: Optional[float] = None,
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
    await self.drop_at_location(
      location,
      clearance_y=clearance_y,
      acceleration_scale_x=acceleration_scale_x,
      minimum_traverse_height_end=minimum_traverse_height_end,
    )
    self._clear_held_state()
    place_resource(held, destination)

  # -- at a location -------------------------------------------------------------------------------
