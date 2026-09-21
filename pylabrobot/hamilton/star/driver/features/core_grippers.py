"""The STAR's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Optional, Tuple

from pylabrobot.hamilton.star.driver.lock import _FirmwareLock
from pylabrobot.resources.deck import Deck

if TYPE_CHECKING:
  from ..master import STARDriver
  from .pipettes import Pipettes
  from .x_arm import XArm


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
      tt=f"{tip_type_index:02}",
      tp=f"{begin_z:04}",
      tz=f"{end_z:04}",
      th=f"{minimum_traverse_height_start:04}",
      pa=f"{back_channel + 1:02}",
      pb=f"{front_channel + 1:02}",
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
    back_channel: int,
    front_channel: int,
  ):
    """Send the tool discard as it is given, in tenths of a millimetre. `C0 ZS`.

    Args:
      x_position: the holder's x; negative sends `xd1`.
      back_channel_y: y of the back channel's tool in the holder.
      front_channel_y: y of the front channel's tool in the holder.
      begin_z: where the deposit begins.
      end_z: where it ends.
      minimum_traverse_height_start: how high the channels travel first.
      minimum_traverse_height_end: where the channels are left.
      back_channel: the back channel, 0-indexed.
      front_channel: the front channel, 0-indexed.
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
      pa=f"{back_channel + 1:02}",
      pb=f"{front_channel + 1:02}",
    )
