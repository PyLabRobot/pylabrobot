"""The STAR's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import dataclasses
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Tuple, cast

from pylabrobot.hamilton.star.driver.errors import STARFirmwareError
from pylabrobot.hamilton.star.driver.lock import _FirmwareLock
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.hamilton.core_gripper_tools import HamiltonCoreGripperTool
from pylabrobot.resources.hamilton.core_grippers import HamiltonCoreGrippers
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder

if TYPE_CHECKING:
  from ..master import STARDriver
  from .pipettes import Pipettes
  from .x_arm import XArm

logger = logging.getLogger(__name__)

# How far below the pick-up window the tools are deposited, in mm: legacy's 235/225 against 215/205.
_DEPOSIT_BELOW_PICK_UP = 20.0


@dataclasses.dataclass
class CoreGrippersConfiguration:
  """Facts of the grip itself; positions, speeds and accelerations are the pipettes'."""

  grip_strength_range: Tuple[int, int] = (0, 99)


class CoreGrippers:
  """The CoRe grip tools a pair of channels carries, and what they take."""

  # Grip strength index, 0 (low) to 99 (high).
  default_grip_strength: int = 15
  # Z speed while a resource is held, in mm/s.
  default_z_speed_with_resource_held: float = 50.0
  # Z acceleration while a resource is held, in mm/s2; 800 jolts a plate.
  default_z_acceleration_with_resource_held: float = 150.0
  # Height the channels travel at and are left at around a tool command, in mm.
  default_minimum_traverse_height: float = 280.0

  def __init__(
    self, driver: "STARDriver", configuration: Optional[CoreGrippersConfiguration] = None
  ) -> None:
    """
    Args:
      driver: the driver to send commands through.
      configuration: the grip's device facts. Defaults to `CoreGrippersConfiguration()`.
    """
    self._driver = driver
    self.configuration = configuration or CoreGrippersConfiguration()
    self._tools_mounted = False
    self._back_channel: Optional[int] = None
    self._front_channel: Optional[int] = None
    # x, rear y, front y, seek z and end z of the pick-up the channels hold the tools from, in mm.
    self._tools_taken_from: Optional[Tuple[float, float, float, float, float]] = None
    # Each mounted tool, the holder it came from and where in it, to put it back in the model.
    self._parked_tools: List[Tuple[HeadTool, Optional[Resource], Optional[Coordinate]]] = []
    self._pickup_distance_from_top: Optional[float] = None
    self._holding_resource_width: Optional[float] = None
    self._held_resource: Optional[Resource] = None
    self._taken_from: Optional[Tuple[Resource, Optional[Coordinate]]] = None

  # -- what carries them ---------------------------------------------------------------------------

  @property
  def arm(self) -> "XArm":
    """The arm carrying these grippers."""
    return next(a for a in self._driver.arms if a.core_grippers is self)

  @property
  def _pipettes(self) -> "Pipettes":
    """The channels that carry the tools.

    Raises:
      RuntimeError: If the arm has no pipettes.
    """
    pipettes = self.arm.pipettes
    if pipettes is None:
      raise RuntimeError("no pipettes to carry the grippers; have you called `star.setup()`?")
    return pipettes

  @property
  def _deck(self) -> Deck:
    """The deck positions are measured from.

    Raises:
      RuntimeError: If the driver was given no deck.
    """
    if self._driver.deck is None:
      raise RuntimeError("the CoRe grippers are placed from the deck; this driver was given none")
    return self._driver.deck

  def _holder(self) -> HamiltonCoreGrippers:
    """The CO-RE gripper holder the deck carries.

    Raises:
      TypeError: If the deck carries none.
    """
    for resource in self._deck.get_all_children():
      if isinstance(resource, HamiltonCoreGrippers):
        return resource
    raise TypeError("the deck carries no CO-RE gripper holder")

  # -- state ---------------------------------------------------------------------------------------

  @property
  def tools_mounted(self) -> bool:
    """Whether the tools are on the channels."""
    return self._tools_mounted

  async def request_tool_attached(self, channel: int) -> bool:
    """Whether the device senses something on `channel` (`C0 RT`).

    Args:
      channel: which channel, 0-indexed from the back.

    Raises:
      ValueError: If the channel does not exist.
    """
    self._pipettes._require_channel(channel)
    return bool((await self._pipettes.sense_tip_presence())[channel])

  def _require_mounted(self) -> None:
    """Raise unless the tools are on the channels.

    Raises:
      RuntimeError: If they are not.
    """
    if not self._tools_mounted:
      raise RuntimeError(
        "the CoRe gripper tools are not mounted. Call "
        "`await star.core_grippers.pick_up_tools()` first, or use "
        "`async with star.core_grippers.mounted():`."
      )

  def _clear_held_state(self) -> None:
    self._holding_resource_width = None
    self._pickup_distance_from_top = None
    self._held_resource = None
    self._taken_from = None

  def _front_tool(self) -> Optional[Resource]:
    """The tool the model has on the front channel, or None while it has none."""
    if self._front_channel is None:
      return None
    shaft = self._pipettes.shaft(self._front_channel)
    return shaft.tip if shaft is not None and shaft.has_tip() else None

  def _hang_held_resource_on_the_front_tool(self) -> None:
    """Hang the held resource from the front tool where the jaws hold it, so it rides with them.

    Its centre at the jaws' centre, its top `pickup_distance_from_top` above the grip line - the
    front channel's stop disc less the tool's overhang. Nothing happens while nothing models them.
    """
    held, from_top, tool = self._held_resource, self._pickup_distance_from_top, self._front_tool()
    if held is None or from_top is None or not isinstance(tool, HamiltonCoreGripperTool):
      return
    back = self._pipettes.get_reference_point_location(cast(int, self._back_channel))
    front = self._pipettes.get_reference_point_location(cast(int, self._front_channel))
    if back is None or front is None:
      return
    grip_line = front.z - (tool.get_size_z() - tool.fitting_depth - tool.grip_line_height)
    center = held.center().rotated(held.get_absolute_rotation())
    lfb = Coordinate(
      front.x - center.x,
      (back.y + front.y) / 2 - center.y,
      grip_line + from_top - held.get_absolute_size_z(),
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

  # -- with a resource held ------------------------------------------------------------------------

  async def _unchecked_fw_move_resource(
    self,
    x_position: int,
    x_acceleration_index: int,
    y_position: int,
    z_position: int,
    z_speed: int,
    minimum_traverse_height_start: int,
  ):
    """Send the held plate's move as it is given, in tenths of a millimetre. `C0 ZM`.

    Args:
      x_position: the plate centre's x; negative sends `xd1`.
      x_acceleration_index: 1 to 5.
      y_position: the plate centre's y.
      z_position: the plate's height.
      z_speed: in tenths of a mm/s.
      minimum_traverse_height_start: how high the channels travel first.
    """
    return await self._driver.send_command(
      module="C0",
      command="ZM",
      subsystem=_FirmwareLock.CHANNELS,
      read_timeout=120,
      xs=f"{abs(x_position):05}",
      xd=int(x_position < 0),
      xg=f"{x_acceleration_index}",
      yj=f"{y_position:04}",
      zj=f"{z_position:04}",
      zy=f"{z_speed:04}",
      th=f"{minimum_traverse_height_start:04}",
    )

  # ----------------------------------------
  # Tools
  # ----------------------------------------

  # -- firmware ------------------------------------------------------------------------------------

  async def _unchecked_fw_pick_up_tools(
    self,
    x_position: int,
    back_channel_y: int,
    front_channel_y: int,
    begin_z: int,
    end_z: int,
    minimum_traverse_height_start: int,
    back_channel: int,
    front_channel: int,
    tip_type_index: int,
  ):
    """Send the tool pick-up as it is given, in tenths of a millimetre. `C0 ZT`.

    Args:
      x_position: the tools' x; negative sends `xd1`.
      back_channel_y: y of the tool the back channel takes.
      front_channel_y: y of the tool the front channel takes.
      begin_z: where the pick-up begins.
      end_z: where it ends.
      minimum_traverse_height_start: how high the channels travel first.
      back_channel: the back channel, 0-indexed.
      front_channel: the front channel, 0-indexed.
      tip_type_index: the tip type table entry the tools are picked up as.
    """
    return await self._driver.send_command(
      module="C0",
      command="ZT",
      subsystem=_FirmwareLock.CHANNELS,
      read_timeout=120,
      xs=f"{abs(x_position):05}",
      xd=int(x_position < 0),
      ya=f"{back_channel_y:04}",
      yb=f"{front_channel_y:04}",
      pa=f"{back_channel + 1:02}",
      pb=f"{front_channel + 1:02}",
      tp=f"{begin_z:04}",
      tz=f"{end_z:04}",
      th=f"{minimum_traverse_height_start:04}",
      tt=f"{tip_type_index:02}",
    )

  async def _unchecked_fw_drop_tools(
    self,
    x_position: int,
    back_channel_y: int,
    front_channel_y: int,
    begin_z: int,
    end_z: int,
    minimum_traverse_height_start: int,
    minimum_traverse_height_end: int,
  ):
    """Send the tool discard as it is given, in tenths of a millimetre. `C0 ZS`.

    Sends no `pa`/`pb`, as legacy: the firmware returns the tools from the channels holding them.

    Args:
      x_position: the holder's x; negative sends `xd1`.
      back_channel_y: y of the back channel's tool in the holder.
      front_channel_y: y of the front channel's tool in the holder.
      begin_z: where the deposit begins.
      end_z: where it ends.
      minimum_traverse_height_start: how high the channels travel first.
      minimum_traverse_height_end: where the channels are left.
    """
    return await self._driver.send_command(
      module="C0",
      command="ZS",
      subsystem=_FirmwareLock.CHANNELS,
      read_timeout=120,
      xs=f"{abs(x_position):05}",
      xd=int(x_position < 0),
      ya=f"{back_channel_y:04}",
      yb=f"{front_channel_y:04}",
      tp=f"{begin_z:04}",
      tz=f"{end_z:04}",
      th=f"{minimum_traverse_height_start:04}",
      te=f"{minimum_traverse_height_end:04}",
    )

  async def _move_to_safe_z_after_failure(self) -> None:
    """`pipettes.move_to_safe_z`, logging rather than raising so the command's error is the one seen."""
    try:
      await self._pipettes.move_to_safe_z()
    except Exception:
      logger.warning("could not raise the channels to safe Z after the failure")

  async def pick_up_tools_at_location(
    self,
    tool_position_x: float,
    tool_position_z: float,
    front_channel_position_y: float,
    rear_channel_position_y: float,
    *,
    tool_seek: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    front_channel: Optional[int] = None,
  ) -> None:
    """Pick up the tools (`C0 ZT`) as tip type 14; to safe Z only if it fails.

    Args:
      tool_position_x: the tools' x, in mm.
      tool_position_z: where the pick-up ends, in mm.
      front_channel_position_y: y of the front channel's tool, in mm.
      rear_channel_position_y: y of the back channel's tool, in mm.
      tool_seek: where the pick-up begins, in mm. `tool_position_z + 10` when None.
      minimum_traverse_height_start: how high the channels travel first, in mm.
        `default_minimum_traverse_height` when None.
      front_channel: 0-indexed; the back tool goes on `front_channel - 1`. The front-most when None.

    Raises:
      RuntimeError: If the channels already hold tools, or the iSWAP is not parked.
      HasTipError: If either channel carries something.
      ValueError: If a position, height or the front channel is out of range.
    """
    pipettes = self._pipettes
    if self._tools_taken_from is not None:
      raise RuntimeError("the channels already hold the tools; `drop_tools()` first")
    if front_channel is None:
      front_channel = pipettes.num_channels - 1
    pipettes._require_channel(front_channel)
    if front_channel == 0:
      raise ValueError(
        "front_channel must be 1 or more: the back tool goes on the channel behind it"
      )
    back_channel = front_channel - 1

    if tool_seek is None:
      tool_seek = tool_position_z + 10.0
    if minimum_traverse_height_start is None:
      minimum_traverse_height_start = self.default_minimum_traverse_height
    if not tool_seek > tool_position_z:
      raise ValueError(f"tool_seek ({tool_seek}) must be above tool_position_z ({tool_position_z})")
    pipettes._check_reachable("x", tool_position_x)
    for y in (rear_channel_position_y, front_channel_position_y):
      pipettes._check_reachable("y", y)
    for z in (tool_seek, tool_position_z, minimum_traverse_height_start):
      pipettes._check_reachable("z", z)
    spacing = pipettes._min_spacing_between(back_channel, front_channel)
    if rear_channel_position_y - front_channel_position_y < spacing:
      raise ValueError(
        f"the rear tool must be at least {spacing} mm behind the front one; got rear "
        f"{rear_channel_position_y}, front {front_channel_position_y}"
      )
    for channel in (back_channel, front_channel):
      shaft = pipettes.shaft(channel)
      if shaft is not None and shaft.has_tip():
        raise HasTipError(f"channel {channel} already carries something")
    await pipettes._require_iswap_parked()

    from ..master import CORE_GRIPPER_TIP_TYPE_INDEX

    try:
      await self._unchecked_fw_pick_up_tools(
        x_position=round(tool_position_x * 10),
        back_channel_y=round(rear_channel_position_y * 10),
        front_channel_y=round(front_channel_position_y * 10),
        begin_z=round(tool_seek * 10),
        end_z=round(tool_position_z * 10),
        minimum_traverse_height_start=round(minimum_traverse_height_start * 10),
        back_channel=back_channel,
        front_channel=front_channel,
        tip_type_index=CORE_GRIPPER_TIP_TYPE_INDEX,
      )
    except BaseException:
      await self._move_to_safe_z_after_failure()
      raise
    finally:
      await pipettes._record_after_tip_command()
    # No safe Z on success: ZT leaves the tools' lowest point at `th`, the traverse height.
    self._back_channel, self._front_channel = back_channel, front_channel
    self._tools_taken_from = (
      tool_position_x,
      rear_channel_position_y,
      front_channel_position_y,
      tool_seek,
      tool_position_z,
    )

  async def drop_tools(
    self,
    *,
    move_to_safe_z_first: bool = True,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Put the tools back where `pick_up_tools_at_location` took them (`C0 ZS`), then safe Z.

    Args:
      move_to_safe_z_first: raise to `default_minimum_traverse_height` before travelling; False
        travels at the height the channels stand.
      minimum_traverse_height_end: where to leave the channels, in mm.
        `default_minimum_traverse_height` when None.

    Raises:
      RuntimeError: If no pick-up is remembered, or the iSWAP is not parked.
      ValueError: If a height is out of range.
    """
    if self._tools_taken_from is None or self._back_channel is None or self._front_channel is None:
      raise RuntimeError("no pick-up to put the tools back to; `pick_up_tools_at_location` first")
    if self._held_resource is not None:
      raise RuntimeError(
        f"the grippers hold {self._held_resource.name}: put it down first with `drop_resource` "
        "or `return_resource`, then return the tools"
      )
    pipettes = self._pipettes
    x, rear_y, front_y, seek, end = self._tools_taken_from
    start = self.default_minimum_traverse_height if move_to_safe_z_first else 0.0
    if minimum_traverse_height_end is None:
      minimum_traverse_height_end = self.default_minimum_traverse_height
    heights = [seek - _DEPOSIT_BELOW_PICK_UP, end - _DEPOSIT_BELOW_PICK_UP]
    for z in heights + [minimum_traverse_height_end] + ([start] if move_to_safe_z_first else []):
      pipettes._check_reachable("z", z)
    await pipettes._require_iswap_parked()

    try:
      await self._unchecked_fw_drop_tools(
        x_position=round(x * 10),
        back_channel_y=round(rear_y * 10),
        front_channel_y=round(front_y * 10),
        begin_z=round(heights[0] * 10),
        end_z=round(heights[1] * 10),
        minimum_traverse_height_start=round(start * 10),
        minimum_traverse_height_end=round(minimum_traverse_height_end * 10),
      )
    except BaseException:
      await self._move_to_safe_z_after_failure()
      raise
    finally:
      await pipettes._record_after_tip_command()
    await pipettes.move_to_safe_z()
    self._tools_taken_from = None
    self._back_channel = self._front_channel = None

  # -- mounting ------------------------------------------------------------------------------------

  def _park_tools(self) -> None:
    """Put the tools back in the holder in the model, where they were taken from."""
    for tool, holder, location in self._parked_tools:
      if tool.parent is not None:
        tool.parent.unassign_child_resource(tool)
      if holder is not None:
        holder.assign_child_resource(tool, location=location)
    self._parked_tools = []

  async def pick_up_tools(self, front_channel: Optional[int] = None) -> None:
    """Take the tools out of the deck's holder and onto two channels.

    Args:
      front_channel: as `pick_up_tools_at_location` takes it.

    Raises:
      RuntimeError: If already mounted, or a channel senses nothing once the pick-up has run.
      TypeError: If the deck carries no holder, or the holder no tools.
    """
    if self._tools_mounted:
      raise RuntimeError("the CoRe gripper tools are already mounted")
    holder = self._holder()
    tools = [child for child in holder.children if isinstance(child, HeadTool)]
    if not tools:
      raise TypeError("the holder carries no CO-RE grip tools to pick up")

    # As legacy: the holder's centre x, its channel y centres, and a 10 mm window below the tops.
    deck = self._deck
    loc = holder.get_location_wrt(deck, x="c")
    top = max(tool.get_location_wrt(deck, z="t").z for tool in tools)
    await self.pick_up_tools_at_location(
      tool_position_x=loc.x,
      tool_position_z=top - 10.0,
      front_channel_position_y=loc.y + holder.front_channel_y_center,
      rear_channel_position_y=loc.y + holder.back_channel_y_center,
      tool_seek=top,
      front_channel=front_channel,
    )
    pair = (cast(int, self._back_channel), cast(int, self._front_channel))

    # A taken tool rides where the channel rides, as a tip does. The back channel takes the rear tool.
    self._parked_tools = [(tool, tool.parent, tool.location) for tool in tools]
    rear_first = sorted(tools, key=lambda tool: tool.get_location_wrt(deck, y="c").y)
    taken = []
    for channel, tool in zip(pair, reversed(rear_first)):
      shaft = self._pipettes.shaft(channel)
      if shaft is not None:
        shaft.mount_tip(tool)
        taken.append(channel)
    await self._pipettes._record_where_they_stopped("z")

    # The command returning is not the tools being on: asked of the device, once per mount.
    presence = await self._pipettes.sense_tip_presence()
    missing = [channel for channel in taken if not presence[channel]]
    if missing:
      self._park_tools()
      await self._pipettes._record_where_they_stopped("z")
      raise RuntimeError(
        f"the pick-up ran, but {'channel' if len(missing) == 1 else 'channels'} "
        f"{', '.join(str(c) for c in missing)} sense nothing. The tools are back in their holder "
        "in the model; check the device before running anything."
      )
    self._tools_mounted = True

  async def return_tools(self) -> None:
    """Put the tools back in their holder, if mounted.

    A drop that fails leaves the channels holding them, and the model says so.
    """
    if not self._tools_mounted:
      return
    await self.drop_tools()
    self._tools_mounted = False
    self._park_tools()
    await self._pipettes._record_where_they_stopped("z")

  @asynccontextmanager
  async def mounted(self, front_channel: Optional[int] = None) -> AsyncIterator["CoreGrippers"]:
    """The tools on for as long as the block runs, and returned on the way out.

    Args:
      front_channel: as `pick_up_tools` takes it.
    """
    await self.pick_up_tools(front_channel=front_channel)
    try:
      yield self
    finally:
      await self.return_tools()

  # ----------------------------------------
  # Resources
  # ----------------------------------------

  def _resolve_pickup_distance(
    self, resource: Resource, pickup_distance_from_top: Optional[float]
  ) -> float:
    """How far below the resource's top the jaws close: given, preferred, else 5 mm (as legacy)."""
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

  # -- firmware ------------------------------------------------------------------------------------

  async def _unchecked_fw_pick_up_resource(
    self,
    x_position: int,
    y_position: int,
    y_gripping_speed: int,
    z_position: int,
    z_speed: int,
    open_gripper_position: int,
    plate_width: int,
    grip_strength: int,
    minimum_traverse_height_start: int,
    minimum_z_position_end: int,
  ):
    """Send the plate pick-up as it is given, in tenths of a millimetre. `C0 ZP`.

    Args:
      x_position: the plate centre's x; negative sends `xd1`.
      y_position: the plate centre's y.
      y_gripping_speed: in tenths of a mm/s.
      z_position: the gripping height.
      z_speed: in tenths of a mm/s.
      open_gripper_position: the jaws' opening before they close.
      plate_width: the width they close to.
      grip_strength: 0 (low) to 99 (high).
      minimum_traverse_height_start: how high the channels travel first.
      minimum_z_position_end: where the channels are left.
    """
    return await self._driver.send_command(
      module="C0",
      command="ZP",
      subsystem=_FirmwareLock.CHANNELS,
      read_timeout=120,
      xs=f"{abs(x_position):05}",
      xd=int(x_position < 0),
      yj=f"{y_position:04}",
      yv=f"{y_gripping_speed:04}",
      zj=f"{z_position:04}",
      zy=f"{z_speed:04}",
      yo=f"{open_gripper_position:04}",
      yg=f"{plate_width:04}",
      yw=f"{grip_strength:02}",
      th=f"{minimum_traverse_height_start:04}",
      te=f"{minimum_z_position_end:04}",
    )

  async def _unchecked_fw_drop_resource(
    self,
    x_position: int,
    y_position: int,
    z_position: int,
    press_on_distance: int,
    z_speed: int,
    open_gripper_position: int,
    minimum_traverse_height_start: int,
    minimum_z_position_end: int,
    x_acceleration_index: Optional[int] = None,
  ):
    """Send the plate put-down as it is given, in tenths of a millimetre. `C0 ZR`.

    Args:
      x_position: the plate centre's x; negative sends `xd1`.
      y_position: the plate centre's y.
      z_position: the deposit height.
      press_on_distance: how far past the deposit height to press, 0 to 999.
      z_speed: in tenths of a mm/s.
      open_gripper_position: the jaws' opening to let go.
      minimum_traverse_height_start: how high the channels travel first.
      minimum_z_position_end: where the channels are left.
      x_acceleration_index: 1 to 5; not sent when None, as legacy.
    """
    parameters: Dict[str, Any] = {"xs": f"{abs(x_position):05}", "xd": int(x_position < 0)}
    if x_acceleration_index is not None:
      parameters["xg"] = f"{x_acceleration_index}"
    parameters.update(
      yj=f"{y_position:04}",
      zj=f"{z_position:04}",
      zi=f"{press_on_distance:03}",
      zy=f"{z_speed:04}",
      yo=f"{open_gripper_position:04}",
      th=f"{minimum_traverse_height_start:04}",
      te=f"{minimum_z_position_end:04}",
    )
    return await self._driver.send_command(
      module="C0", command="ZR", subsystem=_FirmwareLock.CHANNELS, read_timeout=120, **parameters
    )

  async def _unchecked_fw_release_plate(self):
    """Open the gripper and let go of whatever it holds. `C0 ZO`."""
    return await self._driver.send_command(
      module="C0", command="ZO", subsystem=_FirmwareLock.CHANNELS
    )

  # -- probing ------------------------------------------------------------------------------------

  async def probe_z_for_resource_using_ztouch(
    self,
    location: Coordinate,
    resource: Resource,
    gripper_y_margin: float = 1.0,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    enable_recovery: bool = True,
  ) -> bool:
    """Push the open jaws down onto `resource`'s centre: stalling on it is finding it (`C0 ZP`).

    Args:
      location: where the resource's left-front-bottom is, in deck coordinates.
      resource: the resource to check for.
      gripper_y_margin: how far inside each of its front and back walls the jaws come down, in mm.
      offset: added to its centre, in mm.
      minimum_traverse_height_start: how high the channels travel first, in mm.
        `default_minimum_traverse_height` when None.
      minimum_traverse_height_end: where to leave the channels, in mm.
        `default_minimum_traverse_height` when None.
      enable_recovery: ask on the console whether to check again when it is not found.

    Returns:
      True if found.

    Raises:
      RuntimeError: If the tools are not picked up.
      ValueError: If the jaw width is out of range, the check is aborted, or ZP fails otherwise.
    """
    if self._tools_taken_from is None or self._back_channel is None or self._front_channel is None:
      raise RuntimeError(
        "the CoRe gripper tools are not picked up; `pick_up_tools_at_location` first"
      )
    pipettes = self._pipettes
    if minimum_traverse_height_start is None:
      minimum_traverse_height_start = self.default_minimum_traverse_height
    if minimum_traverse_height_end is None:
      minimum_traverse_height_end = self.default_minimum_traverse_height

    center = location + resource.centers()[0] + offset
    y_width_to_gripper_bump = resource.get_absolute_size_y() - gripper_y_margin * 2
    min_width = pipettes._min_spacing_between(self._back_channel, self._front_channel)
    max_width = round(resource.get_absolute_size_y())
    if not min_width <= y_width_to_gripper_bump <= max_width:
      raise ValueError(
        f"width between channels must be between {min_width} and {max_width} mm, is "
        f"{y_width_to_gripper_bump}"
      )

    resource_found = False
    try_counter = 0
    try:
      while not resource_found:
        try:
          await self._unchecked_fw_pick_up_resource(
            x_position=round(center.x * 10),
            y_position=round(center.y * 10),
            y_gripping_speed=50,
            z_position=round(center.z * 10),
            z_speed=600,
            open_gripper_position=round(y_width_to_gripper_bump * 10),
            plate_width=round(y_width_to_gripper_bump * 10),
            grip_strength=20,
            minimum_traverse_height_start=round(minimum_traverse_height_start * 10),
            minimum_z_position_end=round(minimum_traverse_height_end * 10),
          )
        except STARFirmwareError as exc:
          # Trace 62 is the channels' Z drive stalling: the jaws came down on the resource.
          for module_error in exc.errors.values():
            if module_error.trace_information == 62:
              resource_found = True
            else:
              raise ValueError(f"Unexpected error encountered: {exc}") from exc
        else:
          if enable_recovery:
            print(
              f"\nWARNING: Resource '{resource.name}' not found at center"
              f" location {(center.x, center.y, center.z)} during check no {try_counter}."
            )
            user_prompt = input(
              "Have you checked resource is present?"
              "\n [ yes ] -> machine will check location again"
              "\n [ abort ] -> machine will abort run\n Answer:"
            )
            if user_prompt == "yes":
              try_counter += 1
            elif user_prompt == "abort":
              raise ValueError(
                f"Resource '{resource.name}' not found at center"
                f" location {(center.x, center.y, center.z)}"
                " & error not resolved -> aborted resource movement."
              )
          else:
            return False
    finally:
      await pipettes._record_after_tip_command()

    return True
