"""The Hamilton Prep's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Literal, Optional, Tuple

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

# How much further apart the channels stand than the width they are open around: the paddles' faces
# sit inside the channels' centres. PRPAA1087, 2026-09-21: 94.45 around an 85.48 plate at 2.5 mm
# clearance a side, and 95.47 at 3.0 when letting go of it.
JAW_OPEN_EXTRA = 3.97


class CoreGrippers:
  """The CoRe grippers: the tools they grip with, and what they take.

  ``pick_up_tools`` / ``return_tools`` mount the paddles, which nothing is gripped without.
  ``pick_up_resource`` / ``drop_resource`` resolve geometry from the resource tree;
  ``pick_up_at_location`` / ``drop_at_location`` are told the point instead.

  Prep has no grip-force field: ``clearance_y``, ``squeeze_mm`` and ``grip_speed_y`` set how hard
  the jaws close.
  """

  # The Z drives' acceleration while the tools are on, in mm/s2: set as they are picked up, put back
  # as they are returned. None leaves the drives. PRPAA1087, 2026-09-21: 800, the drives' own, jolts
  # a plate; 100 was gentle but slow.
  default_z_acceleration: Optional[float] = 150.0

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
    self._pickup_distance_from_top: Optional[float] = None
    self._holding_resource_width: Optional[float] = None
    self._held_resource: Optional[Resource] = None
    self._plate_top_center: Optional[Coordinate] = None
    self._plate_top_z_offset: Optional[float] = None
    self._taken_from: Optional[Tuple[Resource, Optional[Coordinate]]] = None
    self._tools_mounted = False
    self._parked_tools: List[Tuple[HeadTool, Optional[Resource], Optional[Coordinate]]] = []
    self._z_acceleration_set = False
    self._z_acceleration_before: Optional[Dict[Any, float]] = None

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

  async def request_plate_held(self) -> bool:
    """Whether the firmware records a plate held (PrepGetPlateHeld, cmd=22).

    Its record, not a sensor: a pick-up that closed on nothing sets it too, and it survives a power
    cycle. While it is set the device refuses to initialize and to drop the tools.
    :meth:`release_plate` clears it.
    """
    return bool((await self._driver.send_command(PrepCmd.PrepGetPlateHeld())).value)

  def _require_mounted(self) -> None:
    """Raise unless the tools are on the channels: nothing is gripped without them."""
    if not self._tools_mounted:
      raise RuntimeError(
        "the CoRe gripper tools are not mounted. Call "
        "`await prep.core_grippers.pick_up_tools()` first, or use "
        "`async with prep.core_grippers.mounted():`."
      )

  def _refuse_while_holding(self) -> None:
    """Raise if the jaws are holding something.

    For the moves that drive a channel's own Y, where one sent on its own opens or skews the grip.
    X is the arm both ride, so it is not one of them.
    """
    if self._holding_resource_width is not None:
      raise RuntimeError(
        "the grippers are holding something, and a channel moved on its own opens or skews the "
        "grip. Use `move_resource_to_xy_position`, which moves both together, or put it down "
        "first."
      )

  def _plate_top(self, grip: Coordinate) -> PrepCmd.XYZCoord:
    """The firmware's `plate_top_center` for a plate whose jaws are to be at `grip`.

    Move and drop are told the plate's top and put the jaws the pick-up's offset below it, so a grip
    point sent as the top takes the plate that far too low. Measured on PRPAA1087: 5.00 mm.
    """
    if self._plate_top_z_offset is None:
      raise RuntimeError("Not holding anything")
    return PrepCmd.XYZCoord(
      default_values=False,
      x_position=grip.x,
      y_position=grip.y,
      z_position=grip.z + self._plate_top_z_offset,
    )

  def _clear_held_state(self) -> None:
    self._holding_resource_width = None
    self._pickup_distance_from_top = None
    self._held_resource = None
    self._plate_top_center = None
    self._plate_top_z_offset = None
    self._taken_from = None

  # -- z -------------------------------------------------------------------------------------------

  @asynccontextmanager
  async def _z_acceleration(self, z_acceleration: Optional[float] = None) -> AsyncIterator[None]:
    """The Z drives at `z_acceleration` for the enclosed moves, then back to the mounted setting.

    None sends nothing: the drives are at `default_z_acceleration` for as long as the tools are on.
    Set by the outermost call; calls inside it leave the drives be.
    """
    if z_acceleration is None or self._z_acceleration_set:
      yield
      return
    self._z_acceleration_set = True
    try:
      async with self._pipettes._z_drive_acceleration(z_acceleration):
        yield
    finally:
      self._z_acceleration_set = False

  async def _mount_z_acceleration(self) -> None:
    """Keep what the Z drives are at, and set them to `default_z_acceleration`."""
    if self.default_z_acceleration is None or self._z_acceleration_before is not None:
      return
    before: Dict[Any, float] = {}
    for drive in [c.zdrive for c in self._pipettes.channels if c.zdrive is not None]:
      answer = await self._driver.send_command(PrepCmd.PrepZDriveGetAcceleration(dest=drive))
      before[drive] = float(answer.value)
      await self._driver.send_command(
        PrepCmd.PrepZDriveSetAcceleration(dest=drive, value=self.default_z_acceleration)
      )
    self._z_acceleration_before = before

  async def _unmount_z_acceleration(self) -> None:
    """Put the Z drives back to what they were at before the tools were picked up."""
    before, self._z_acceleration_before = self._z_acceleration_before, None
    for drive, value in (before or {}).items():
      try:
        await self._driver.send_command(PrepCmd.PrepZDriveSetAcceleration(dest=drive, value=value))
      except Exception:
        logger.warning(
          "could not put the Z drive acceleration at %s back to %s mm/s2",
          drive,
          value,
          exc_info=True,
        )

  async def _raise_to_traverse(self, minimum_traverse_height_end: Optional[float]) -> None:
    """Leave the channels where they can travel.

    Args:
      minimum_traverse_height_end: where to leave them, in mm. None goes to Z safety, as high as
        they reach, at the height the firmware picks for itself.
    """
    if minimum_traverse_height_end is None:
      await self._pipettes.move_to_safe_z()
      return
    await self._pipettes.move_tool_bottom_to_z_positions(
      {channel: minimum_traverse_height_end for channel in range(self._pipettes.num_channels)}
    )

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
    """Pick up the tools (PrepPickUpTool, cmd=15), over them first so the pick-up is straight down.

    Sets the Z drives to `default_z_acceleration` for as long as the tools are on.
    """
    await self._mount_z_acceleration()
    async with self._z_acceleration():
      if tool_seek is None:
        tool_seek = tool_position_z + 10.0
      if tip_definition is None:
        tip_definition = PrepCmd.CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS
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
      except BaseException:
        # Down at the tools, worked or not, and the next lateral move would drag them through the
        # holder. They did not go on, so the drives go back too.
        await self._pipettes.move_to_safe_z()
        await self._pipettes._record_channel_bounds()
        await self._unmount_z_acceleration()
        raise
      await self._pipettes.move_to_safe_z()
      await self._pipettes._record_channel_bounds()

  async def drop_tools(self, *, move_to_safe_z_first: bool = True) -> None:
    """Put the tools back where they were taken from (PrepDropTool, cmd=16).

    The firmware carries them there itself, from wherever the channels stand (PRPAA1087,
    2026-09-21). The Z drives go back to what they were at before the tools went on.
    """
    async with self._z_acceleration():
      if move_to_safe_z_first:
        await self._pipettes.move_to_safe_z()
      await self._driver.send_command(PrepCmd.PrepDropTool())
      await self._pipettes._record_channel_bounds()
    await self._unmount_z_acceleration()

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

    # The command returning is not the tools being on: asked of the device, once per mount.
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
    """Move the tools along X. See :meth:`XArm.move_to_x_position`.

    Allowed while holding: both channels ride the one arm, so the jaws keep their spacing.
    `z_acceleration` None leaves the drives at `default_z_acceleration`.
    """
    async with self._z_acceleration(z_acceleration):
      self._require_mounted()
      await self._x_arm.move_to_x_position(
        x,
        speed=speed,
        acceleration=acceleration,
        minimum_traverse_height_start=minimum_traverse_height_start,
        z_speed=z_speed,
        z_acceleration=None,
      )

  async def move_to_y_position(self, channel: int, y: float, speed: Optional[float] = None) -> None:
    """Move one tool along Y. See :meth:`Pipettes.move_to_y_position`."""
    async with self._z_acceleration():
      self._require_mounted()
      self._refuse_while_holding()
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
    """Move the tools across the deck. See :meth:`Pipettes.move_to_xy_positions`.

    `z_acceleration` None leaves the drives at `default_z_acceleration`.
    """
    async with self._z_acceleration(z_acceleration):
      self._require_mounted()
      self._refuse_while_holding()
      await self._pipettes.move_to_xy_positions(
        x,
        ys,
        make_space=make_space,
        minimum_traverse_height_start=minimum_traverse_height_start,
        via_lane=via_lane,
        x_speed=x_speed,
        x_speed_scale=x_speed_scale,
        z_speed=z_speed,
        z_acceleration=None,
      )

  async def move_to_safe_z(self, channels: Optional[List[int]] = None) -> None:
    """Raise the tools to Z safety. See :meth:`Pipettes.move_to_safe_z`."""
    async with self._z_acceleration():
      self._require_mounted()
      await self._pipettes.move_to_safe_z(channels)

  # -- with a resource held ------------------------------------------------------------------------

  async def move_to_location(
    self,
    location: Coordinate,
    *,
    acceleration_scale_x: int = 1,
  ) -> None:
    """Move a held plate to a new position without releasing it.

    Args:
      location: where the jaws are to hold it, as `pick_up_at_location` takes it.
      acceleration_scale_x: X-axis acceleration scale.
    """
    async with self._z_acceleration():
      plate_top_center = self._plate_top(location)
      await self._driver.send_command(
        PrepCmd.PrepMovePlate(
          plate_top_center=plate_top_center,
          acceleration_scale_x=acceleration_scale_x,
        )
      )

  async def move_resource_to_xy_position(
    self,
    x: Optional[float] = None,
    y: Optional[float] = None,
    *,
    acceleration_scale_x: int = 1,
  ) -> None:
    """Carry the held resource across the deck, at the height it is already at (PrepMovePlate).

    Both jaws move together; a channel moved on its own would open or skew the grip.

    Args:
      x: where to take its centre, in mm. None keeps it where it is in x.
      y: where to take its centre, in mm. None keeps it where it is in y.
      acceleration_scale_x: X-axis acceleration scale.

    Raises:
      ValueError: If neither x nor y is given.
      RuntimeError: If nothing is held, or it was gripped without an offset being recorded.
    """
    if x is None and y is None:
      raise ValueError("give x, y or both: with neither there is nowhere to move it")
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    if self._plate_top_z_offset is None:
      raise RuntimeError(
        "the offset the plate is held at was not recorded, so it cannot be carried in the plane "
        "alone. Use `move_to_location`, which is told all three."
      )
    # Where it is, asked of the device: the pick-up raises it, and the arm may move it in x. A
    # channel's z is where its jaw holds the plate, centred between the two.
    jaw_0, jaw_1 = (await self._pipettes.request_locations())[:2]
    x = jaw_0.x if x is None else x
    y = (jaw_0.y + jaw_1.y) / 2 if y is None else y
    await self.move_to_location(
      Coordinate(x, y, jaw_0.z), acceleration_scale_x=acceleration_scale_x
    )
    self._plate_top_center = Coordinate(x, y, jaw_0.z + self._plate_top_z_offset)

  # ----------------------------------------
  # Resources
  # ----------------------------------------
  # -- geometry ------------------------------------------------------------------------------------

  def _resolve_pickup_distance(
    self, resource: Resource, pickup_distance_from_top: Optional[float]
  ) -> float:
    """How far below the resource's top the jaws close: given, preferred, else 5 mm (as STAR)."""
    if pickup_distance_from_top is not None:
      return pickup_distance_from_top
    if resource.preferred_pickup_location is not None:
      logger.debug(
        "Using preferred pickup location for resource %s as pickup_distance_from_top was "
        "not specified.",
        resource.name,
      )
      return resource.get_absolute_size_z() - resource.preferred_pickup_location.z
    logger.debug(
      "No preferred pickup location for resource %s. Using default pickup distance of 5mm "
      "from top.",
      resource.name,
    )
    return 5.0

  def _resource_width(self, resource: Resource) -> float:
    if self._grip_axis == "y":
      return resource.get_absolute_size_y()
    return resource.get_absolute_size_x()

  def _pickup_location(
    self,
    resource: Resource,
    offset: Coordinate,
    pickup_distance_from_top: float,
  ) -> Coordinate:
    center = resource.center().rotated(resource.get_absolute_rotation())
    if resource.is_in_subtree_of(self._deck):
      loc = resource.get_location_wrt(self._deck, "l", "f", "b") + center + offset
    else:
      loc = center + offset
    return Coordinate(
      loc.x, loc.y, loc.z + resource.get_absolute_size_z() - pickup_distance_from_top
    )

  def _drop_location(
    self, destination: Resource, offset: Coordinate, child: Optional[Coordinate] = None
  ) -> Coordinate:
    if self._held_resource is None or self._pickup_distance_from_top is None:
      raise RuntimeError(
        "drop_resource requires a prior pick_up_resource (held resource and grip height)."
      )
    held = self._held_resource
    from_top = self._pickup_distance_from_top
    if child is None:
      child = (
        destination.get_default_child_location(held)
        if isinstance(destination, ResourceHolder)
        else Coordinate.zero()
      )
    center = held.center().rotated(held.get_absolute_rotation())
    plate_lfb = destination.get_location_wrt(self._deck, "l", "f", "b") + child
    loc = plate_lfb + center + offset
    return Coordinate(loc.x, loc.y, loc.z + held.get_absolute_size_z() - from_top)

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
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Pick up a plate at a grip point. Holds no :class:`Resource`: use ``drop_at_location``.

    Args:
      location: Plate center at grip height (x, y, grip_z) in deck coordinates.
      resource_width: Plate width along the grip axis (Y) in mm.
      resource_length: Plate length (X) in mm.
      resource_height: Plate height (Z) in mm.
      plate_top_z_offset: Offset from grip Z to plate top center Z.
      clearance_y: Approach clearance along the grip axis (mm).
      grip_speed_y: Grip speed (mm/s).
      squeeze_mm: Additional squeeze distance beyond clearance (mm).
      minimum_traverse_height_start: the height to travel to the plate at, in mm. None goes to Z
        safety, as high as they reach.
      minimum_traverse_height_end: the height to leave the channels at once it is gripped, in mm.
        None goes to Z safety.
      z_acceleration: the Z drives' acceleration for every Z move of the grip, in mm/s2, then
        restored. None leaves them at `default_z_acceleration`.
    """
    async with self._z_acceleration(z_acceleration):
      self._require_mounted()
      await self._raise_to_traverse(minimum_traverse_height_start)
      # Over the plate first, jaws open, so the pick-up is straight down: left to itself the
      # firmware dives across the deck (to 73 mm on PRPAA1087). 0 raises nothing: they are already
      # up, and with the tools on the pipettes' traverse height is more than they reach.
      half = (resource_width + 2 * clearance_y + JAW_OPEN_EXTRA) / 2
      await self._pipettes.move_to_xy_positions(
        location.x, {0: location.y + half, 1: location.y - half}, minimum_traverse_height_start=0
      )
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
      self._plate_top_center = Coordinate(location.x, location.y, location.z + plate_top_z_offset)
      self._plate_top_z_offset = plate_top_z_offset
      self._holding_resource_width = resource_width
      self._pickup_distance_from_top = None
      self._held_resource = None
      self._taken_from = None

  async def drop_at_location(
    self,
    location: Coordinate,
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Let go of the held plate at a grip point.

    Args:
      location: where the jaws are to hold it when it is let go, as `pick_up_at_location` takes it.
      clearance_y: Release clearance along the grip axis (mm).
      acceleration_scale_x: X-axis acceleration scale.
      minimum_traverse_height_start: the height to carry it to the destination at, in mm. None
        goes to Z safety, as high as they reach.
      minimum_traverse_height_end: the height to leave the channels at once it is released, in mm.
        None goes to Z safety.
      z_acceleration: the Z drives' acceleration for every Z move of letting go, in mm/s2, then
        restored. None leaves them at `default_z_acceleration`.
    """
    async with self._z_acceleration(z_acceleration):
      if self._holding_resource_width is None:
        raise RuntimeError("Not holding anything")
      await self._raise_to_traverse(minimum_traverse_height_start)
      # Carried over the destination first, so letting go is straight down: left to itself the
      # firmware dives across the deck with the plate.
      await self.move_resource_to_xy_position(
        location.x, location.y, acceleration_scale_x=acceleration_scale_x
      )
      plate_top_center = self._plate_top(location)
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
    self._clear_held_state()

  # -- by resource ---------------------------------------------------------------------------------

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    *,
    resource_width: Optional[float] = None,
    resource_length: Optional[float] = None,
    resource_height: Optional[float] = None,
    clearance_y: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Grip a resource where the tree has it.

    Args:
      pickup_distance_from_top: how far below its top the jaws close, in mm. None is its preferred
        pickup location, else 5 mm.
    """
    self._require_mounted()
    source = (resource.parent, resource.location)
    from_top = self._resolve_pickup_distance(resource, pickup_distance_from_top)
    if resource_width is None:
      resource_width = self._resource_width(resource)
    if resource_length is None:
      resource_length = resource.get_absolute_size_x()
    if resource_height is None:
      resource_height = resource.get_absolute_size_z()

    location = self._pickup_location(resource, offset, from_top)
    await self.pick_up_at_location(
      location,
      resource_width,
      resource_length=resource_length,
      resource_height=resource_height,
      plate_top_z_offset=from_top,
      clearance_y=clearance_y,
      grip_speed_y=grip_speed_y,
      squeeze_mm=squeeze_mm,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_end=minimum_traverse_height_end,
      z_acceleration=z_acceleration,
    )
    self._pickup_distance_from_top = from_top
    self._holding_resource_width = resource_width
    self._held_resource = resource
    source_parent, source_location = source
    self._taken_from = None if source_parent is None else (source_parent, source_location)

  async def _drop(
    self,
    destination: Resource,
    child: Optional[Coordinate],
    offset: Coordinate,
    *,
    clearance_y: float,
    acceleration_scale_x: int,
    minimum_traverse_height_start: Optional[float],
    minimum_traverse_height_end: Optional[float],
    z_acceleration: Optional[float],
  ) -> None:
    """Put the held resource down in `destination`, at `child` or where it would put it."""
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    if self._held_resource is None or self._pickup_distance_from_top is None:
      raise RuntimeError(
        "drop_resource requires a prior pick_up_resource (held resource and grip height)."
      )
    held = self._held_resource
    destination.check_can_drop_resource_here(held)
    location = self._drop_location(destination, offset, child)
    await self.drop_at_location(
      location,
      clearance_y=clearance_y,
      acceleration_scale_x=acceleration_scale_x,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_end=minimum_traverse_height_end,
      z_acceleration=z_acceleration,
    )
    place_resource(held, destination, location=child)

  async def drop_resource(
    self,
    destination: Resource,
    offset: Coordinate = Coordinate.zero(),
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Put the held resource down in `destination`; the tree follows once it is down."""
    await self._drop(
      destination,
      None,
      offset,
      clearance_y=clearance_y,
      acceleration_scale_x=acceleration_scale_x,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_end=minimum_traverse_height_end,
      z_acceleration=z_acceleration,
    )

  async def return_resource(
    self,
    offset: Coordinate = Coordinate.zero(),
    *,
    clearance_y: float = 3.0,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Put the held resource back where :meth:`pick_up_resource` took it from.

    Raises:
      RuntimeError: If nothing is held, or it was not taken from a parent in the tree.
    """
    if self._taken_from is None:
      raise RuntimeError(
        "nothing to return it to: return_resource needs a pick_up_resource of a resource that "
        "had a parent."
      )
    parent, location = self._taken_from
    await self._drop(
      parent,
      location,
      offset,
      clearance_y=clearance_y,
      acceleration_scale_x=acceleration_scale_x,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_end=minimum_traverse_height_end,
      z_acceleration=z_acceleration,
    )
