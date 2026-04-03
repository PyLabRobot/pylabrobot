"""Vantage IPG (Integrated Plate Gripper) backend: translates arm operations into firmware commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from pylabrobot.capabilities.arms.backend import OrientableGripperArmBackend
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate

if TYPE_CHECKING:
  from .driver import VantageDriver


def _direction_degrees_to_grip_orientation(degrees: float) -> int:
  """Convert rotation angle in degrees to Vantage IPG grip orientation code.

  The IPG uses numeric codes 1-44 for various orientations. The primary ones used for
  plate manipulation are:
    32 = front grip (default)
    11 = right grip (90 degrees)
    31 = back grip (180 degrees)
    12 = left grip (270 degrees)
  """
  normalized = round(degrees) % 360
  mapping = {0: 32, 90: 11, 180: 31, 270: 12}
  if normalized not in mapping:
    raise ValueError(f"grip direction must be a multiple of 90 degrees, got {degrees}")
  return mapping[normalized]


class IPGBackend(OrientableGripperArmBackend):
  """Backend for the Vantage Integrated Plate Gripper (IPG).

  Implements OrientableGripperArmBackend, translating arm operations into
  firmware commands on module A1RM.
  """

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver
    self._parked: bool = False

  @property
  def parked(self) -> bool:
    return self._parked

  async def _on_setup(self) -> None:
    pass

  async def _on_stop(self) -> None:
    if not self._parked:
      try:
        await self.park()
      except Exception:
        pass

  # -- BackendParams ---------------------------------------------------------

  @dataclass
  class PickUpParams(BackendParams):
    grip_strength: int = 100
    open_gripper_position: int = 860
    plate_width_tolerance: int = 20
    acceleration_index: int = 4
    z_clearance_height: int = 50
    hotel_depth: int = 0
    minimal_height_at_command_end: int = 3600

  @dataclass
  class DropParams(BackendParams):
    open_gripper_position: int = 860
    z_clearance_height: int = 50
    press_on_distance: int = 5
    hotel_depth: int = 0
    minimal_height_at_command_end: int = 3600

  # -- OrientableGripperArmBackend interface ---------------------------------

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, IPGBackend.PickUpParams):
      backend_params = IPGBackend.PickUpParams()

    grip_orientation = _direction_degrees_to_grip_orientation(direction)
    th = round(self.driver.traversal_height * 10)

    await self._ipg_prepare_gripper_orientation(
      grip_orientation=grip_orientation,
      minimal_traverse_height_at_begin_of_command=th,
    )

    await self._ipg_grip_plate(
      x_position=round(location.x * 10),
      y_position=round(location.y * 10),
      z_position=round(location.z * 10),
      grip_strength=backend_params.grip_strength,
      open_gripper_position=backend_params.open_gripper_position,
      plate_width=round(resource_width * 10),
      plate_width_tolerance=backend_params.plate_width_tolerance,
      acceleration_index=backend_params.acceleration_index,
      z_clearance_height=backend_params.z_clearance_height,
      hotel_depth=backend_params.hotel_depth,
      minimal_height_at_command_end=backend_params.minimal_height_at_command_end,
    )
    self._parked = False

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, IPGBackend.DropParams):
      backend_params = IPGBackend.DropParams()

    await self._ipg_put_plate(
      x_position=round(location.x * 10),
      y_position=round(location.y * 10),
      z_position=round(location.z * 10),
      open_gripper_position=backend_params.open_gripper_position,
      z_clearance_height=backend_params.z_clearance_height,
      hotel_depth=backend_params.hotel_depth,
      minimal_height_at_command_end=backend_params.minimal_height_at_command_end,
    )

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    th = round(self.driver.traversal_height * 10)
    await self._ipg_move_to_defined_position(
      x_position=round(location.x * 10),
      y_position=round(location.y * 10),
      z_position=round(location.z * 10),
      minimal_traverse_height_at_begin_of_command=th,
    )

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    pass  # No explicit halt command for IPG.

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    await self.driver.send_command(module="A1RM", command="GP")
    self._parked = True

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    raise NotImplementedError(
      "request_gripper_location is not yet implemented for the Vantage IPG. "
      "The firmware response format for A1RM:QI needs to be reverse-engineered."
    )

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    await self.driver.send_command(module="A1RM", command="DO")

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    # Closing is handled implicitly by grip_plate with the desired width.
    pass

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    return not self._parked

  # -- Initialization and status queries -------------------------------------

  async def request_initialization_status(self) -> bool:
    """Check if the IPG module is initialized (A1RM:QW)."""
    resp = await self.driver.send_command(module="A1RM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def initialize(self) -> None:
    """Initialize the IPG (A1RM:DI)."""
    await self.driver.send_command(module="A1RM", command="DI")

  async def get_parking_status(self) -> bool:
    """Check if the IPG is parked (A1RM:RG). Returns True if parked."""
    resp = await self.driver.send_command(module="A1RM", command="RG", fmt={"rg": "int"})
    parked = resp is not None and resp["rg"] == 1
    self._parked = parked
    return parked

  # -- firmware commands (A1RM) ----------------------------------------------

  async def _ipg_prepare_gripper_orientation(
    self,
    grip_orientation: int = 32,
    minimal_traverse_height_at_begin_of_command: int = 3600,
  ) -> None:
    """Prepare gripper orientation (A1RM:GA)."""
    await self.driver.send_command(
      module="A1RM",
      command="GA",
      gd=grip_orientation,
      th=minimal_traverse_height_at_begin_of_command,
    )

  async def _ipg_grip_plate(
    self,
    x_position: int,
    y_position: int,
    z_position: int,
    grip_strength: int = 100,
    open_gripper_position: int = 860,
    plate_width: int = 800,
    plate_width_tolerance: int = 20,
    acceleration_index: int = 4,
    z_clearance_height: int = 50,
    hotel_depth: int = 0,
    minimal_height_at_command_end: int = 3600,
  ) -> None:
    """Grip plate (A1RM:DG)."""
    await self.driver.send_command(
      module="A1RM",
      command="DG",
      xp=x_position,
      yp=y_position,
      zp=z_position,
      yw=grip_strength,
      yo=open_gripper_position,
      yg=plate_width,
      pt=plate_width_tolerance,
      ai=acceleration_index,
      zc=z_clearance_height,
      hd=hotel_depth,
      te=minimal_height_at_command_end,
    )

  async def _ipg_put_plate(
    self,
    x_position: int,
    y_position: int,
    z_position: int,
    open_gripper_position: int = 860,
    z_clearance_height: int = 50,
    hotel_depth: int = 0,
    minimal_height_at_command_end: int = 3600,
  ) -> None:
    """Put plate (A1RM:DR)."""
    await self.driver.send_command(
      module="A1RM",
      command="DR",
      xp=x_position,
      yp=y_position,
      zp=z_position,
      yo=open_gripper_position,
      zc=z_clearance_height,
      hd=hotel_depth,
      te=minimal_height_at_command_end,
    )

  async def _ipg_move_to_defined_position(
    self,
    x_position: int,
    y_position: int,
    z_position: int,
    minimal_traverse_height_at_begin_of_command: int = 3600,
  ) -> None:
    """Move to defined position (A1RM:DN)."""
    await self.driver.send_command(
      module="A1RM",
      command="DN",
      xp=x_position,
      yp=y_position,
      zp=z_position,
      th=minimal_traverse_height_at_begin_of_command,
    )
