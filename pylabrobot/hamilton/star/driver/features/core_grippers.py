"""The STAR's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Optional, Tuple

from pylabrobot.hamilton.star.driver.lock import _FirmwareLock
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.errors import HasTipError

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

  # -- state ---------------------------------------------------------------------------------------

  @property
  def tools_mounted(self) -> bool:
    """Whether the tools are on the channels."""
    return self._tools_mounted

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
    back_channel: Optional[int] = None,
    front_channel: Optional[int] = None,
  ) -> None:
    """Pick up the tools (`C0 ZT`) as tip type 14, then raise every channel to safe Z.

    Args:
      tool_position_x: the tools' x, in mm.
      tool_position_z: where the pick-up ends, in mm.
      front_channel_position_y: y of the front channel's tool, in mm.
      rear_channel_position_y: y of the back channel's tool, in mm.
      tool_seek: where the pick-up begins, in mm. `tool_position_z + 10` when None.
      minimum_traverse_height_start: how high the channels travel first, in mm.
        `default_minimum_traverse_height` when None.
      back_channel: 0-indexed. `front_channel - 1` when None.
      front_channel: 0-indexed. `back_channel + 1`, or the front-most, when None.

    Raises:
      RuntimeError: If the channels already hold tools, or the iSWAP is not parked.
      HasTipError: If either channel carries something.
      ValueError: If a position, height or channel pair is out of range.
    """
    pipettes = self._pipettes
    if self._tools_taken_from is not None:
      raise RuntimeError("the channels already hold the tools; `drop_tools()` first")
    if back_channel is None and front_channel is None:
      front_channel = pipettes.num_channels - 1
    if front_channel is None:
      assert back_channel is not None
      front_channel = back_channel + 1
    if back_channel is None:
      back_channel = front_channel - 1
    for channel in (back_channel, front_channel):
      pipettes._require_channel(channel)
    if front_channel != back_channel + 1:
      raise ValueError(
        f"the tools go on two adjacent channels, back then front; got {back_channel} and "
        f"{front_channel}"
      )

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
    await pipettes.move_to_safe_z()
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
