"""The Hamilton Prep's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, List, Literal, Optional, Tuple

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
# sit inside the channels' centres: 94.45 around an 85.48 plate at 2.5 mm clearance a side, and
# 95.47 at 3.0 when letting go of it.
JAW_OPEN_EXTRA = 3.97
# With a Z speed, how far above the let-go a held plate is lowered at it: PrepDropPlate makes its
# own Z move at ~133 mm/s whatever is set, so it is left this much.
FIRMWARE_Z_LEG = 1.0


class CoreGrippers:
  """The CoRe grippers: the tools they grip with, and what they take.

  ``pick_up_tools`` / ``return_tools`` mount the paddles, which nothing is gripped without.
  ``pick_up_resource`` / ``drop_resource`` / ``return_resource`` move resources, into a
  destination or to a point on the deck; ``release_plate`` opens in place, for recovery.

  Prep has no grip-force field: ``y_clearance``, ``squeeze_mm`` and ``grip_speed_y`` set how hard
  the jaws close.
  """

  default_z_acceleration_with_resource_held: Optional[float] = 150.0

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

  @property
  def _back_channel(self) -> int:
    """The channel that takes the back tool: channels[-2]."""
    return self._pipettes.num_channels - 2

  @property
  def _front_channel(self) -> int:
    """The channel that takes the front tool: channels[-1]."""
    return self._pipettes.num_channels - 1

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

  def _compute_plate_top(self, grip: Coordinate) -> PrepCmd.XYZCoord:
    """The firmware's `plate_top_center` for a plate whose jaws are to be at `grip`.

    Move and drop are told the plate's top and put the jaws the pick-up's offset below it, so a grip
    point sent as the top takes the plate that far too low: 5.00 mm.
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

  def _front_tool(self) -> Optional[Resource]:
    """The tool the model has on the front channel, or None while it has none."""
    shaft = self._pipettes.shaft(self._front_channel)
    return shaft.tip if shaft is not None and shaft.has_tip() else None

  def _hang_held_resource_on_the_front_tool(self) -> None:
    """Hang the held resource from the front tool where the jaws hold it, so it rides with them.

    Its centre at the jaws' centre, its top `pickup_distance_from_top` above the grip line - where the
    device reports the front channel with the tools on. Nothing happens while nothing models them.
    """
    held, from_top, tool = self._held_resource, self._pickup_distance_from_top, self._front_tool()
    if held is None or from_top is None or tool is None:
      return
    back = self._pipettes.get_reference_point_location(self._back_channel)
    front = self._pipettes.get_reference_point_location(self._front_channel)
    if back is None or front is None:
      return
    center = held.center().rotated(held.get_absolute_rotation())
    lfb = Coordinate(
      front.x - center.x,
      (back.y + front.y) / 2 - center.y,
      front.z + from_top - held.get_absolute_size_z(),
    )
    held.unassign()
    tool.assign_child_resource(held, location=lfb - tool.get_location_wrt(self._deck))

  def _put_held_resource_on_the_deck(self) -> None:
    """Put a resource hanging from the front tool on the deck, where it is now."""
    held, tool = self._held_resource, self._front_tool()
    if held is None or tool is None or held.parent is not tool:
      return
    where = held.get_location_wrt(self._deck)
    held.unassign()
    self._deck.assign_child_resource(held, location=where)

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  # -- Memory of Speed & Acceleration --------------------------------------------------------------

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

  async def _move_jaws_to_z(self, z: float, speed: float) -> None:
    """Move both jaws to `z` together at `speed`, in mm and mm/s (MoveZAbsolute)."""
    await self._pipettes.move_tool_bottom_to_z_positions(
      {self._back_channel: z, self._front_channel: z}, speed=speed
    )

  # ---- z -----------------------------------------------------------------------------------------

  async def _set_z_acceleration(self, z_acceleration: float) -> None:
    """Set every Z drive's acceleration, in mm/s2."""
    for drive in [c.zdrive for c in self._pipettes.channels if c.zdrive is not None]:
      await self._driver.send_command(
        PrepCmd.PrepZDriveSetAcceleration(dest=drive, value=z_acceleration)
      )

  async def _restore_z_acceleration(self) -> None:
    """Put the Z drives back to the acceleration setup read."""
    await self._set_z_acceleration(self._pipettes.default_z_acceleration)

  @asynccontextmanager
  async def _temporary_z_drive_acceleration(
    self, z_acceleration: Optional[float] = None, *, holding: Optional[bool] = None
  ) -> AsyncIterator[None]:
    """The Z drives at `z_acceleration` for the enclosed moves, then back to the pipettes' default.

    None is `default_z_acceleration_with_resource_held` while a resource is held, and sends nothing
    otherwise. `holding` None asks the grippers. Set by the outermost call; calls inside it leave
    the drives be.
    """
    if holding is None:
      holding = self._holding_resource_width is not None
    if z_acceleration is None and holding:
      z_acceleration = self.default_z_acceleration_with_resource_held
    if z_acceleration is None or self._z_acceleration_set:
      yield
      return
    self._z_acceleration_set = True
    try:
      await self._set_z_acceleration(z_acceleration)
      yield
    finally:
      self._z_acceleration_set = False
      await self._restore_z_acceleration()

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

    Allowed while holding: both channels ride the one arm, so the jaws keep their spacing. Unlike a
    carry, it takes `speed` and `acceleration`. `z_acceleration` None is the held default.
    """
    async with self._temporary_z_drive_acceleration(z_acceleration):
      self._require_mounted()
      await self._x_arm.move_to_x_position(
        x,
        speed=speed,
        acceleration=acceleration,
        minimum_traverse_height_start=minimum_traverse_height_start,
        z_speed=z_speed,
        z_acceleration=None,
      )

  async def move_to_y_positions(self, ys: List[float], speed: Optional[float] = None) -> None:
    """Move the tools along Y together. See :meth:`Pipettes.move_to_y_positions`.

    Args:
      ys: target y of each tool in mm, from the back.
      speed: speed in mm/s. `pipettes.default_y_speed` when None.
    """
    self._require_mounted()
    self._refuse_while_holding()
    await self._pipettes.move_to_y_positions(
      dict(zip((self._back_channel, self._front_channel), ys)), speed=speed
    )

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

    `z_acceleration` None leaves the drives at the pipettes' default.
    """
    async with self._temporary_z_drive_acceleration(z_acceleration):
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
    async with self._temporary_z_drive_acceleration():
      self._require_mounted()
      await self._pipettes.move_to_safe_z(channels)

  # -- with a resource held ------------------------------------------------------------------------

  async def _unchecked_fw_move_resource(
    self, plate_top_center: PrepCmd.XYZCoord, acceleration_scale_x: int
  ) -> None:
    """Send `Pipettor.MovePlate` (PrepMovePlate, cmd=19) without checks.

    Moves X, Y and Z at once: a target at another height is reached along an arc, plate held.
    `move_resource_to_xy_position` sends the height it is already at.

    Carries X at its own profile, whatever the X axis is set to: about 125 mm/s and 160 mm/s2 over
    117 mm on the device, with `acceleration_scale_x` 1 or 2 alike.

    Args:
      plate_top_center: where the held plate's top centre goes, in deck coordinates.
      acceleration_scale_x: X-axis acceleration scale; showed no effect at 2.
    """
    await self._driver.send_command(
      PrepCmd.PrepMovePlate(
        plate_top_center=plate_top_center, acceleration_scale_x=acceleration_scale_x
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
      acceleration_scale_x: X-axis acceleration scale; showed no effect at 2.

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
        "the offset the plate is held at was not recorded, so where its top is cannot be worked "
        "out. Put it down and pick it up with `pick_up_resource`."
      )
    # Where it is, asked of the device: the pick-up raises it, and the arm may move it in x. A
    # channel's z is where its jaw holds the plate, centred between the two.
    locations = await self._pipettes.request_locations()
    back, front = locations[self._back_channel], locations[self._front_channel]
    x = back.x if x is None else x
    y = (back.y + front.y) / 2 if y is None else y
    try:
      await self._unchecked_fw_move_resource(
        self._compute_plate_top(Coordinate(x, y, back.z)), acceleration_scale_x=acceleration_scale_x
      )
    finally:
      await self._pipettes._record_where_they_stopped()
    self._plate_top_center = Coordinate(x, y, back.z + self._plate_top_z_offset)

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
    """Pick up the tools (PrepPickUpTool, cmd=15), over them first so the pick-up is straight down."""
    if tool_seek is None:
      tool_seek = tool_position_z + 10.0
    if tip_definition is None:
      tip_definition = PrepCmd.CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS
    await self._pipettes.move_to_xy_positions(
      tool_position_x,
      {self._back_channel: rear_channel_position_y, self._front_channel: front_channel_position_y},
      make_space=True,
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
      # holder.
      await self._pipettes.move_to_safe_z()
      await self._pipettes._record_channel_bounds()
      raise
    finally:
      await self._pipettes._record_where_they_stopped()
    await self._pipettes.move_to_safe_z()
    await self._pipettes._record_channel_bounds()

  async def drop_tools(self, *, move_to_safe_z_first: bool = True) -> None:
    """Put the tools back where they were taken from (PrepDropTool, cmd=16).

    The firmware carries them there itself, from wherever the channels stand.

    Raises:
      RuntimeError: If they hold something: the firmware refuses (0x0F04).
    """
    if self._held_resource is not None:
      raise RuntimeError(
        f"the grippers hold {self._held_resource.name}: put it down first with `drop_resource` "
        "or `return_resource`, then return the tools"
      )
    if await self.request_plate_held():
      raise RuntimeError(
        "the device reports a plate held, though none was picked up here: put it down with "
        "`release_plate` first, then return the tools"
      )
    if move_to_safe_z_first:
      await self._pipettes.move_to_safe_z()
    try:
      await self._driver.send_command(PrepCmd.PrepDropTool())
    finally:
      await self._pipettes._record_where_they_stopped()
    await self._pipettes._record_channel_bounds()

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

    # A taken tool rides where the channel rides, as a tip does. The back channel takes the rear tool.
    self._parked_tools = [(tool, tool.parent, tool.location) for tool in tools]
    rear_first = sorted(tools, key=lambda tool: tool.get_location_wrt(deck, y="c").y)
    taken = []
    for channel, tool in zip((self._back_channel, self._front_channel), reversed(rear_first)):
      shaft = self._pipettes.shaft(channel)
      if shaft is not None:
        shaft.mount_tip(tool)
        taken.append(channel)
    # Read once the tools are on the model: Z is reported at their jaws.
    await self._pipettes._record_where_they_stopped()

    # The command returning is not the tools being on: asked of the device, once per mount.
    missing = [channel for channel in taken if not await self.request_tool_attached(channel)]
    if missing:
      self._park_tools()
      await self._pipettes._record_where_they_stopped()
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
    # Read once the tools are off the model: Z is reported at the shafts' ends again.
    await self._pipettes._record_where_they_stopped()

  @asynccontextmanager
  async def mounted(self) -> AsyncIterator["CoreGrippers"]:
    """The tools on for as long as the block runs, and returned on the way out."""
    await self.pick_up_tools()
    try:
      yield self
    finally:
      await self.return_tools()

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

  def _compute_resource_width(self, resource: Resource) -> float:
    if self._grip_axis == "y":
      return resource.get_absolute_size_y()
    return resource.get_absolute_size_x()

  def _compute_pickup_location(
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

  def _compute_drop_location(
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

  # -- at a grip point -----------------------------------------------------------------------------

  async def _pick_up_at(
    self,
    location: Coordinate,
    resource_width: float,
    *,
    resource_length: float,
    resource_height: float,
    plate_top_z_offset: float,
    y_clearance: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    on_gripped: Optional[Callable[[], None]] = None,
  ) -> None:
    """Pick up a plate at a grip point; `pick_up_resource` sets what it holds.

    Args:
      location: Plate center at grip height (x, y, grip_z) in deck coordinates.
      resource_width: Plate width along the grip axis (Y) in mm.
      resource_length: Plate length (X) in mm.
      resource_height: Plate height (Z) in mm.
      plate_top_z_offset: Offset from grip Z to plate top center Z.
      y_clearance: how far each gripper stands from the resource, either side, as it moves in to
        grip it and out after letting go, in mm.
      grip_speed_y: Grip speed (mm/s).
      squeeze_mm: Additional squeeze distance beyond clearance (mm).
      minimum_traverse_height_start: the height to travel to the plate at, in mm. None goes to Z
        safety, as high as they reach.
      minimum_traverse_height_end: the height to leave the channels at once it is gripped, in mm.
        None goes to Z safety.
      z_acceleration: the Z drives' acceleration from the grip on, in mm/s2, then restored. None
        is `default_z_acceleration_with_resource_held`.
      z_speed: how fast the jaws rise with the plate, in mm/s. None leaves it to the firmware
        (~133 mm/s). The way down, empty, is the firmware's.
      on_gripped: called once the jaws have closed, before they rise.
    """
    self._require_mounted()
    await self._raise_to_traverse(minimum_traverse_height_start)
    # Over the plate first, jaws open, so the pick-up is straight down: left to itself the
    # firmware dives across the deck (to 73 mm). 0 raises nothing: they are already
    # up, and with the tools on the pipettes' traverse height is more than they reach.
    half = (resource_width + 2 * y_clearance + JAW_OPEN_EXTRA) / 2
    await self._pipettes.move_to_xy_positions(
      location.x,
      {self._back_channel: location.y + half, self._front_channel: location.y - half},
      make_space=True,
      minimum_traverse_height_start=0,
    )
    raise_to: Optional[float] = None
    if z_speed is not None:
      here = (await self._pipettes.request_locations())[self._back_channel].z
      raise_to = here if minimum_traverse_height_end is None else minimum_traverse_height_end
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
    grip_distance = y_clearance + squeeze_mm

    # Held from the grip on: the raise after it carries the plate.
    async with self._temporary_z_drive_acceleration(z_acceleration, holding=True):
      try:
        await self._driver.send_command(
          PrepCmd.PrepPickUpPlate(
            plate_top_center=plate_top_center,
            plate=plate_dims,
            clearance_y=y_clearance,
            grip_speed_y=grip_speed_y,
            grip_distance=grip_distance,
            grip_height=location.z,
          )
        )
      finally:
        await self._pipettes._record_where_they_stopped()
      # Held now, raised or not: recorded before the jaws rise, so the model rises with them.
      self._plate_top_center = Coordinate(location.x, location.y, location.z + plate_top_z_offset)
      self._plate_top_z_offset = plate_top_z_offset
      self._holding_resource_width = resource_width
      self._pickup_distance_from_top = None
      self._held_resource = None
      self._taken_from = None
      if on_gripped is not None:
        on_gripped()
      if z_speed is None or raise_to is None:
        await self._raise_to_traverse(minimum_traverse_height_end)
      else:
        await self._move_jaws_to_z(raise_to, z_speed)

  async def _drop_at(
    self,
    location: Coordinate,
    *,
    y_clearance: float = 2.5,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    on_released: Optional[Callable[[], None]] = None,
  ) -> None:
    """Let go of the held plate at a grip point.

    Args:
      location: where the jaws are to hold it when it is let go, as `_pick_up_at` takes it.
      y_clearance: how far each gripper stands from the resource, either side, as it moves in to
        grip it and out after letting go, in mm.
      acceleration_scale_x: X-axis acceleration scale; showed no effect at 2.
      minimum_traverse_height_start: the height to carry it to the destination at, in mm. None
        goes to Z safety, as high as they reach.
      minimum_traverse_height_end: the height to leave the channels at once it is released, in mm.
        None goes to Z safety.
      z_acceleration: the Z drives' acceleration until it is let go, in mm/s2, then restored. None
        is `default_z_acceleration_with_resource_held`.
      z_speed: how fast it is lowered to where it is let go, in mm/s. None leaves it to the
        firmware (~133 mm/s).
      on_released: called once the jaws have let go, before they rise.
    """
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    # The firmware lowers it as part of letting go.
    async with self._temporary_z_drive_acceleration(z_acceleration):
      await self._raise_to_traverse(minimum_traverse_height_start)
      # Carried over the destination first, so letting go is straight down: left to itself the
      # firmware dives across the deck with the plate.
      await self.move_resource_to_xy_position(
        location.x, location.y, acceleration_scale_x=acceleration_scale_x
      )
      if z_speed is not None:
        await self._move_jaws_to_z(location.z + FIRMWARE_Z_LEG, z_speed)
      plate_top_center = self._compute_plate_top(location)
      try:
        await self._driver.send_command(
          PrepCmd.PrepDropPlate(
            plate_top_center=plate_top_center,
            clearance_y=y_clearance,
            acceleration_scale_x=acceleration_scale_x,
          )
        )
      finally:
        await self._pipettes._record_where_they_stopped()
      # Let go of now: put down in the model before the jaws rise without it.
      if on_released is not None:
        on_released()
      self._clear_held_state()
    await self._raise_to_traverse(minimum_traverse_height_end)

  async def release_plate(self) -> None:
    """Open the CoRe gripper and release whatever is held (PrepReleasePlate, cmd=21).

    A held resource goes on the deck where it was let go: what the jaws do is unmeasured.
    """
    try:
      await self._driver.send_command(PrepCmd.PrepReleasePlate())
    finally:
      await self._pipettes._record_where_they_stopped()
    self._put_held_resource_on_the_deck()
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
    y_clearance: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
  ) -> None:
    """Grip a resource where the tree has it, and hold it on the front tool in the model.

    Args:
      resource: what to grip.
      offset: added to the grip point, in mm.
      pickup_distance_from_top: how far below its top the jaws close, in mm. None is its preferred
        pickup location, else 5 mm.
      resource_width: its size along the grip axis, in mm. None reads it from the resource.
      resource_length: its size in x, in mm. None reads it from the resource.
      resource_height: its size in z, in mm. None reads it from the resource.
      y_clearance: how far each gripper stands from the resource, either side, as it moves in to
        grip it and out after letting go, in mm.
      grip_speed_y: how fast the jaws close, in mm/s.
      squeeze_mm: how far past touching the jaws close, in mm.
      minimum_traverse_height_start: the height to travel to it at, in mm. None goes to Z safety.
      minimum_traverse_height_end: the height to leave it at once gripped, in mm. None goes to Z
        safety.
      z_acceleration: the Z drives' acceleration from the grip on, in mm/s2, then restored. None is
        `default_z_acceleration_with_resource_held`.
      z_speed: how fast the jaws rise with it, in mm/s. None leaves it to the firmware (~133 mm/s).
        The way down, empty, is the firmware's.

    Raises:
      RuntimeError: If the tools are not mounted.
    """
    self._require_mounted()
    source = (resource.parent, resource.location)
    from_top = self._resolve_pickup_distance(resource, pickup_distance_from_top)
    if resource_width is None:
      resource_width = self._compute_resource_width(resource)
    if resource_length is None:
      resource_length = resource.get_absolute_size_x()
    if resource_height is None:
      resource_height = resource.get_absolute_size_z()

    location = self._compute_pickup_location(resource, offset, from_top)

    def gripped() -> None:
      self._pickup_distance_from_top = from_top
      self._holding_resource_width = resource_width
      self._held_resource = resource
      source_parent, source_location = source
      self._taken_from = None if source_parent is None else (source_parent, source_location)
      self._hang_held_resource_on_the_front_tool()

    await self._pick_up_at(
      location,
      resource_width,
      resource_length=resource_length,
      resource_height=resource_height,
      plate_top_z_offset=from_top,
      y_clearance=y_clearance,
      grip_speed_y=grip_speed_y,
      squeeze_mm=squeeze_mm,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_end=minimum_traverse_height_end,
      z_acceleration=z_acceleration,
      z_speed=z_speed,
      on_gripped=gripped,
    )

  async def drop_resource(
    self,
    destination: Optional[Resource] = None,
    coordinate: Optional[Coordinate] = None,
    offset: Coordinate = Coordinate.zero(),
    *,
    y_clearance: float = 2.5,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
  ) -> None:
    """Put the held resource down; the tree follows once it is down.

    Args:
      destination: the resource it goes into, e.g. a PrepDeck spot.
      coordinate: where its centre-centre-bottom goes, in deck coordinates. It joins the deck there.
      offset: added to where it is let go.
      y_clearance: how far each gripper stands from the resource, either side, as it moves in to
        grip it and out after letting go, in mm.
      z_speed: how fast it is lowered to where it is let go, in mm/s. None leaves it to the
        firmware (~133 mm/s).

    Raises:
      ValueError: If neither or both of `destination` and `coordinate` are given.
    """
    if destination is None and coordinate is None:
      raise ValueError(
        "drop_resource needs to know where the held resource goes: give `destination`, the "
        "resource it goes into, or `coordinate`, its centre-centre-bottom on the deck."
      )
    if destination is not None and coordinate is not None:
      raise ValueError(
        f"drop_resource was given both `destination` ({destination.name}) and `coordinate` "
        f"({coordinate}), and each says where the held resource goes: give one."
      )
    child: Optional[Coordinate] = None
    if coordinate is not None:
      if self._held_resource is None:
        raise RuntimeError(
          "drop_resource requires a prior pick_up_resource (held resource and grip height)."
        )
      # The deck is the destination, and the child location is where the centre-bottom puts the
      # resource's own origin.
      center = self._held_resource.center().rotated(self._held_resource.get_absolute_rotation())
      destination = self._deck
      child = coordinate - Coordinate(center.x, center.y, 0)
    assert destination is not None
    if self._holding_resource_width is None:
      raise RuntimeError("Not holding anything")
    if self._held_resource is None or self._pickup_distance_from_top is None:
      raise RuntimeError(
        "drop_resource requires a prior pick_up_resource (held resource and grip height)."
      )
    held = self._held_resource
    destination.check_can_drop_resource_here(held)
    location = self._compute_drop_location(destination, offset, child)
    await self._drop_at(
      location,
      y_clearance=y_clearance,
      acceleration_scale_x=acceleration_scale_x,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_end=minimum_traverse_height_end,
      z_acceleration=z_acceleration,
      z_speed=z_speed,
      on_released=lambda: place_resource(held, destination, location=child),
    )

  async def return_resource(
    self,
    offset: Coordinate = Coordinate.zero(),
    *,
    y_clearance: float = 2.5,
    acceleration_scale_x: int = 1,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    z_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
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
    kwargs: Dict[str, Any] = {
      "offset": offset,
      "y_clearance": y_clearance,
      "acceleration_scale_x": acceleration_scale_x,
      "minimum_traverse_height_start": minimum_traverse_height_start,
      "minimum_traverse_height_end": minimum_traverse_height_end,
      "z_acceleration": z_acceleration,
      "z_speed": z_speed,
    }
    parent, location = self._taken_from
    if isinstance(parent, ResourceHolder):
      await self.drop_resource(parent, **kwargs)
      return
    # Nothing the grippers reach is off the deck, so where it stood is a point on it.
    held = self._held_resource
    assert held is not None
    center = held.center().rotated(held.get_absolute_rotation())
    lfb = parent.get_location_wrt(self._deck, "l", "f", "b") + location
    await self.drop_resource(coordinate=lfb + Coordinate(center.x, center.y, 0), **kwargs)
