"""Vantage CoreGripper: CoRe gripper backend for Hamilton Vantage liquid handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from pylabrobot.capabilities.arms.backend import GripperArmBackend
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate

if TYPE_CHECKING:
  from .driver import VantageDriver


class VantageCoreGripper(GripperArmBackend):
  """Backend for the Vantage CoRe gripper tools.

  CoRe grippers are tools that mount on two PIP channels and grip plates along the
  Y-axis. Unlike the IPG (which is a dedicated arm), the CoRe gripper shares the PIP
  channels' X/Y/Z drives.

  Firmware commands use module ``A1PM`` with commands ``DG`` (grip), ``DR`` (put),
  ``DH`` (move), ``DO`` (release), ``DJ`` (discard tool).
  """

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver

  async def _on_setup(self):
    pass

  async def _on_stop(self):
    pass

  # -- BackendParams ---------------------------------------------------------

  @dataclass
  class PickUpParams(BackendParams):
    """Vantage-specific parameters for CoRe gripper plate pickup.

    Args:
      grip_strength: Grip strength (0 = low, 99 = high). Default 30.
      z_speed: Z speed in mm/s. Default 128.7.
      open_gripper_position: Open gripper position in mm. Default 86.0.
      acceleration_index: Acceleration index (0-4). Default 4.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height in mm.
        If None, uses driver's traversal_height.
      minimal_height_at_command_end: Minimal height at command end in mm.
        If None, uses driver's traversal_height.
    """

    grip_strength: int = 30
    z_speed: float = 128.7
    open_gripper_position: float = 86.0
    acceleration_index: int = 4
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None

  @dataclass
  class DropParams(BackendParams):
    """Vantage-specific parameters for CoRe gripper plate drop.

    Args:
      press_on_distance: Press-on distance in mm. Default 0.5.
      z_speed: Z speed in mm/s. Default 128.7.
      open_gripper_position: Open gripper position in mm. Default 86.0.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height in mm.
        If None, uses driver's traversal_height.
      minimal_height_at_command_end: Minimal height at command end in mm.
        If None, uses driver's traversal_height.
    """

    press_on_distance: float = 0.5
    z_speed: float = 128.7
    open_gripper_position: float = 86.0
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None

  @dataclass
  class MoveParams(BackendParams):
    """Vantage-specific parameters for CoRe gripper move.

    Args:
      z_speed: Z speed in mm/s. Default 128.7.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height in mm.
        If None, uses driver's traversal_height.
    """

    z_speed: float = 128.7
    minimal_traverse_height_at_begin_of_command: Optional[float] = None

  # -- GripperArmBackend interface -------------------------------------------

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Grip a plate at the given location.

    Args:
      location: Absolute (x, y, z) position of the plate center in mm.
      resource_width: Width of the resource to grip in mm.
      backend_params: Optional :class:`VantageCoreGripper.PickUpParams`.
    """
    if not isinstance(backend_params, VantageCoreGripper.PickUpParams):
      backend_params = VantageCoreGripper.PickUpParams()

    th = self.driver.traversal_height
    open_pos = resource_width + 3.2
    plate_w = resource_width - 3.3

    await self._grip_plate(
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
      z_speed=backend_params.z_speed,
      open_gripper_position=open_pos,
      plate_width=plate_w,
      acceleration_index=backend_params.acceleration_index,
      grip_strength=backend_params.grip_strength,
      minimal_traverse_height_at_begin_of_command=(
        backend_params.minimal_traverse_height_at_begin_of_command
        if backend_params.minimal_traverse_height_at_begin_of_command is not None
        else th
      ),
      minimal_height_at_command_end=(
        backend_params.minimal_height_at_command_end
        if backend_params.minimal_height_at_command_end is not None
        else th
      ),
    )

  async def drop_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Place a plate at the given location.

    Args:
      location: Absolute (x, y, z) position to place the plate in mm.
      resource_width: Width of the resource being placed in mm.
      backend_params: Optional :class:`VantageCoreGripper.DropParams`.
    """
    if not isinstance(backend_params, VantageCoreGripper.DropParams):
      backend_params = VantageCoreGripper.DropParams()

    th = self.driver.traversal_height
    open_pos = resource_width + 3.2

    await self._put_plate(
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
      press_on_distance=backend_params.press_on_distance,
      z_speed=backend_params.z_speed,
      open_gripper_position=open_pos,
      minimal_traverse_height_at_begin_of_command=(
        backend_params.minimal_traverse_height_at_begin_of_command
        if backend_params.minimal_traverse_height_at_begin_of_command is not None
        else th
      ),
      minimal_height_at_command_end=(
        backend_params.minimal_height_at_command_end
        if backend_params.minimal_height_at_command_end is not None
        else th
      ),
    )

  async def move_to_location(
    self,
    location: Coordinate,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move held plate to a new position.

    Args:
      location: Absolute (x, y, z) target position in mm.
      backend_params: Optional :class:`VantageCoreGripper.MoveParams`.
    """
    if not isinstance(backend_params, VantageCoreGripper.MoveParams):
      backend_params = VantageCoreGripper.MoveParams()

    th = self.driver.traversal_height

    await self._move_to_position(
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
      z_speed=backend_params.z_speed,
      minimal_traverse_height_at_begin_of_command=(
        backend_params.minimal_traverse_height_at_begin_of_command
        if backend_params.minimal_traverse_height_at_begin_of_command is not None
        else th
      ),
    )

  async def open_gripper(
    self,
    gripper_width: float,
    backend_params: Optional[BackendParams] = None,
    first_pip_channel_node_no: int = 1,
  ) -> None:
    """Release the gripped object (A1PM:DO).

    Args:
      gripper_width: Ignored for CoRe gripper (width is not controllable on release).
      backend_params: Optional backend params (unused).
      first_pip_channel_node_no: First (lower) pip channel node number (1-16). Default 1.
    """
    await self.driver.send_command(module="A1PM", command="DO", pa=first_pip_channel_node_no)

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    pass  # Grip happens in pick_up_at_location.

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    raise NotImplementedError()

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    pass

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    pass  # Tool management (pick up / return) is handled by the Vantage device class.

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    raise NotImplementedError("request_gripper_location is not implemented for VantageCoreGripper.")

  # -- Firmware commands (A1PM) ----------------------------------------------

  async def _grip_plate(
    self,
    x_position: float,
    y_position: float,
    z_position: float,
    z_speed: float = 128.7,
    open_gripper_position: float = 86.0,
    plate_width: float = 80.0,
    acceleration_index: int = 4,
    grip_strength: int = 30,
    minimal_traverse_height_at_begin_of_command: float = 360.0,
    minimal_height_at_command_end: float = 360.0,
  ):
    """Grip plate using CoRe grippers (A1PM:DG).

    Args:
      x_position: Plate center X position [mm].
      y_position: Plate center Y position [mm].
      z_position: Plate center Z position [mm].
      z_speed: Z speed [mm/s].
      open_gripper_position: Open gripper position [mm].
      plate_width: Plate width [mm].
      acceleration_index: Acceleration index (0-4).
      grip_strength: Grip strength (0-99).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
      minimal_height_at_command_end: Minimal height at command end [mm].
    """
    await self.driver.send_command(
      module="A1PM",
      command="DG",
      xa=round(x_position * 10),
      yj=round(y_position * 10),
      zj=round(z_position * 10),
      zy=round(z_speed * 10),
      yo=round(open_gripper_position * 10),
      yg=round(plate_width * 10),
      ai=acceleration_index,
      yw=grip_strength,
      th=[round(minimal_traverse_height_at_begin_of_command * 10)] * self.driver.num_channels,
      te=[round(minimal_height_at_command_end * 10)] * self.driver.num_channels,
    )

  async def _put_plate(
    self,
    x_position: float,
    y_position: float,
    z_position: float,
    press_on_distance: float = 0.5,
    z_speed: float = 128.7,
    open_gripper_position: float = 86.0,
    minimal_traverse_height_at_begin_of_command: float = 360.0,
    minimal_height_at_command_end: float = 360.0,
  ):
    """Put plate using CoRe grippers (A1PM:DR).

    Args:
      x_position: Plate center X position [mm].
      y_position: Plate center Y position [mm].
      z_position: Plate center Z position [mm].
      press_on_distance: Press-on distance [mm].
      z_speed: Z speed [mm/s].
      open_gripper_position: Open gripper position [mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
      minimal_height_at_command_end: Minimal height at command end [mm].
    """
    await self.driver.send_command(
      module="A1PM",
      command="DR",
      xa=round(x_position * 10),
      yj=round(y_position * 10),
      zj=round(z_position * 10),
      zi=round(press_on_distance * 10),
      zy=round(z_speed * 10),
      yo=round(open_gripper_position * 10),
      th=[round(minimal_traverse_height_at_begin_of_command * 10)] * self.driver.num_channels,
      te=[round(minimal_height_at_command_end * 10)] * self.driver.num_channels,
    )

  async def _move_to_position(
    self,
    x_position: float,
    y_position: float,
    z_position: float,
    z_speed: float = 128.7,
    minimal_traverse_height_at_begin_of_command: float = 360.0,
  ):
    """Move to position with CoRe grippers (A1PM:DH).

    Args:
      x_position: Plate center X position [mm].
      y_position: Plate center Y position [mm].
      z_position: Plate center Z position [mm].
      z_speed: Z speed [mm/s].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
    """
    await self.driver.send_command(
      module="A1PM",
      command="DH",
      xa=round(x_position * 10),
      yj=round(y_position * 10),
      zj=round(z_position * 10),
      zy=round(z_speed * 10),
      th=[round(minimal_traverse_height_at_begin_of_command * 10)] * self.driver.num_channels,
    )

  async def discard_tool(
    self,
    x_position: float = 0.0,
    first_gripper_tool_y_pos: float = 300.0,
    tip_type: Optional[List[int]] = None,
    begin_z_deposit_position: Optional[List[float]] = None,
    end_z_deposit_position: Optional[List[float]] = None,
    first_pip_channel_node_no: int = 1,
    minimal_traverse_height_at_begin_of_command: float = 360.0,
    minimal_height_at_command_end: float = 360.0,
  ):
    """Discard CoRe gripper tool (A1PM:DJ).

    Args:
      x_position: Gripper tool X position [mm].
      first_gripper_tool_y_pos: First (lower channel) CoRe gripper tool Y position [mm].
      tip_type: Tip type per channel. Default ``[4] * num_channels``.
      begin_z_deposit_position: Begin Z deposit position per channel [mm].
        Default ``[0.0] * num_channels``.
      end_z_deposit_position: End Z deposit position per channel [mm].
        Default ``[0.0] * num_channels``.
      first_pip_channel_node_no: First (lower) pip channel node number (1-16).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
      minimal_height_at_command_end: Minimal height at command end [mm].
    """
    n = self.driver.num_channels
    if tip_type is None:
      tip_type = [4] * n
    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0.0] * n
    if end_z_deposit_position is None:
      end_z_deposit_position = [0.0] * n

    await self.driver.send_command(
      module="A1PM",
      command="DJ",
      xa=round(x_position * 10),
      yj=round(first_gripper_tool_y_pos * 10),
      tt=tip_type,
      tp=[round(v * 10) for v in begin_z_deposit_position],
      tz=[round(v * 10) for v in end_z_deposit_position],
      th=[round(minimal_traverse_height_at_begin_of_command * 10)] * n,
      pa=first_pip_channel_node_no,
      te=[round(minimal_height_at_command_end * 10)] * n,
    )
