"""The STAR's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from pylabrobot.hamilton.star.driver.lock import _FirmwareLock

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

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  # -- with a resource held ------------------------------------------------------------------------

  async def _unchecked_fw_move_resource(
    self,
    x_position: int,
    x_acceleration_level: int,
    y_position: int,
    z_position: int,
    z_speed: int,
    minimum_traverse_height_start: int,
  ):
    """Send the held plate's move as it is given, in tenths of a millimetre. `C0 ZM`.

    Args:
      x_position: the plate centre's x; negative sends `xd1`.
      x_acceleration_level: 1 to 5.
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
      xg=f"{x_acceleration_level}",
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

  # ----------------------------------------
  # Resources
  # ----------------------------------------

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
    x_acceleration_level: Optional[int] = None,
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
      x_acceleration_level: 1 to 5; not sent when None, as legacy.
    """
    parameters: Dict[str, Any] = {"xs": f"{abs(x_position):05}", "xd": int(x_position < 0)}
    if x_acceleration_level is not None:
      parameters["xg"] = f"{x_acceleration_level}"
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
