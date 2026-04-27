"""Vantage IPG (Integrated Plate Gripper) backend."""

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

  The IPG uses numeric codes 1-44 for various orientations. The primary ones:
    32 = front grip (default), 11 = right (90), 31 = back (180), 12 = left (270).
  """
  normalized = round(degrees) % 360
  mapping = {0: 32, 90: 11, 180: 31, 270: 12}
  if normalized not in mapping:
    raise ValueError(f"grip direction must be a multiple of 90 degrees, got {degrees}")
  return mapping[normalized]


class IPGBackend(OrientableGripperArmBackend):
  """Backend for the Vantage Integrated Plate Gripper (IPG), module A1RM."""

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver
    self._parked: bool = False

  @property
  def parked(self) -> bool:
    return self._parked

  async def _on_setup(self) -> None:
    """Check IPG initialization status, initialize if needed, and park if not parked."""
    initialized = await self.request_initialization_status()
    if not initialized:
      await self.initialize()
    if not await self.get_parking_status():
      await self.park()

  async def _on_stop(self) -> None:
    if not self._parked:
      try:
        await self.park()
      except Exception:
        pass

  # -- BackendParams ---------------------------------------------------------

  @dataclass
  class PickUpParams(BackendParams):
    """Vantage IPG-specific parameters for gripping a plate.

    Args:
      grip_strength: Grip strength (0-160). Default 81 — the raw firmware default
        of 100 risks crushing thin-skirted, PCR, and magnetic-rack labware.
      plate_width_tolerance: Plate width tolerance [mm]. Default 2.0.
      acceleration_index: Acceleration index (0-4). Default 4.
      z_clearance_height: Z clearance height [mm]. Default 5.0.
      hotel_depth: Hotel depth [mm] (0 = stack mode). Default 0.
      minimal_height_at_command_end: Minimum Z height at command end [mm]. Default 360.0.
    """

    grip_strength: int = 81
    plate_width_tolerance: float = 2.0
    acceleration_index: int = 4
    z_clearance_height: float = 5.0
    hotel_depth: float = 0.0
    minimal_height_at_command_end: float = 360.0

  @dataclass
  class DropParams(BackendParams):
    """Vantage IPG-specific parameters for placing a plate.

    Args:
      z_clearance_height: Z clearance height [mm]. Default 5.0.
      press_on_distance: Accepted for API compatibility but not forwarded to the
        firmware — see ``put_plate``. Default 0.5.
      hotel_depth: Hotel depth [mm] (0 = stack mode). Default 0.
      minimal_height_at_command_end: Minimum Z height at command end [mm]. Default 360.0.
    """

    z_clearance_height: float = 5.0
    press_on_distance: float = 0.5
    hotel_depth: float = 0.0
    minimal_height_at_command_end: float = 360.0

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
    await self.prepare_gripper_orientation(
      grip_orientation=grip_orientation,
      minimal_traverse_height_at_begin_of_command=self.driver.traversal_height,
    )
    await self.grip_plate(
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
      grip_strength=backend_params.grip_strength,
      open_gripper_position=resource_width + 3.2,
      plate_width=resource_width - 3.3,
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

    await self.put_plate(
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
      open_gripper_position=resource_width + 3.2,
      z_clearance_height=backend_params.z_clearance_height,
      press_on_distance=backend_params.press_on_distance,
      hotel_depth=backend_params.hotel_depth,
      minimal_height_at_command_end=backend_params.minimal_height_at_command_end,
    )

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self.move_to_defined_position(
      x_position=location.x,
      y_position=location.y,
      z_position=location.z,
      minimal_traverse_height_at_begin_of_command=self.driver.traversal_height,
    )

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    """Halt the IPG mid-motion.

    Raises:
      NotImplementedError: Not yet ported from legacy Vantage backend.
    """
    raise NotImplementedError("halt is not implemented for the Vantage IPG.")

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Park the IPG (A1RM:GP)."""
    await self.driver.send_command(module="A1RM", command="GP")
    self._parked = True

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    """Return the current gripper location from firmware state.

    Raises:
      NotImplementedError: Not yet ported from legacy Vantage backend.
    """
    raise NotImplementedError(
      "request_gripper_location is not yet implemented for the Vantage IPG."
    )

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Release object (A1RM:DO). The ``gripper_width`` parameter is ignored — the IPG
    only supports fully opening the gripper."""
    await self.driver.send_command(module="A1RM", command="DO")

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Close the IPG to the specified width.

    Raises:
      NotImplementedError: Not yet ported from legacy Vantage backend — the IPG closes
        implicitly during pick_up_at_location (A1RM:DG), not via a standalone command.
    """
    raise NotImplementedError("close_gripper is not implemented for the Vantage IPG.")

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    """Return whether the IPG gripper is currently closed.

    Raises:
      NotImplementedError: Not yet ported from legacy Vantage backend.
    """
    raise NotImplementedError("is_gripper_closed is not implemented for the Vantage IPG.")

  # -- Initialization and status ---------------------------------------------

  async def request_initialization_status(self) -> bool:
    """Check if the IPG is initialized (A1RM:QW)."""
    resp = await self.driver.send_command(module="A1RM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def initialize(self) -> None:
    """Initialize the IPG (A1RM:DI)."""
    await self.driver.send_command(module="A1RM", command="DI")

  async def get_parking_status(self) -> bool:
    """Check if the IPG is parked (A1RM:RG)."""
    resp = await self.driver.send_command(module="A1RM", command="RG", fmt={"rg": "int"})
    parked = resp is not None and resp["rg"] == 1
    self._parked = parked
    return parked

  # -- Firmware commands (A1RM) — all accept standard PLR units (mm) ---------

  async def prepare_gripper_orientation(
    self,
    grip_orientation: int = 32,
    minimal_traverse_height_at_begin_of_command: float = 360.0,
  ) -> None:
    """Prepare gripper orientation (A1RM:GA).

    Args:
      grip_orientation: Grip orientation code (1-44). Default 32 (front).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
    """
    await self.driver.send_command(
      module="A1RM",
      command="GA",
      gd=grip_orientation,
      th=round(minimal_traverse_height_at_begin_of_command * 10),
    )

  async def grip_plate(
    self,
    x_position: float,
    y_position: float,
    z_position: float,
    grip_strength: int = 81,
    open_gripper_position: float = 86.0,
    plate_width: float = 80.0,
    plate_width_tolerance: float = 2.0,
    acceleration_index: int = 4,
    z_clearance_height: float = 5.0,
    hotel_depth: float = 0.0,
    minimal_height_at_command_end: float = 360.0,
  ) -> None:
    """Grip plate (A1RM:DG).

    Args:
      x_position: X position [mm].
      y_position: Y position [mm].
      z_position: Z position [mm].
      grip_strength: Grip strength (0-160).
      open_gripper_position: Open gripper position [mm].
      plate_width: Plate width [mm].
      plate_width_tolerance: Plate width tolerance [mm].
      acceleration_index: Acceleration index (0-4).
      z_clearance_height: Z clearance height [mm].
      hotel_depth: Hotel depth [mm] (0 = stack).
      minimal_height_at_command_end: Minimal height at command end [mm].
    """
    await self.driver.send_command(
      module="A1RM",
      command="DG",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      zp=round(z_position * 10),
      yw=grip_strength,
      yo=round(open_gripper_position * 10),
      yg=round(plate_width * 10),
      pt=round(plate_width_tolerance * 10),
      ai=acceleration_index,
      zc=round(z_clearance_height * 10),
      hd=round(hotel_depth * 10),
      te=round(minimal_height_at_command_end * 10),
    )

  async def put_plate(
    self,
    x_position: float,
    y_position: float,
    z_position: float,
    open_gripper_position: float = 86.0,
    z_clearance_height: float = 5.0,
    press_on_distance: float = 0.5,
    hotel_depth: float = 0.0,
    minimal_height_at_command_end: float = 360.0,
  ) -> None:
    """Put plate (A1RM:DR).

    Args:
      x_position: X position [mm].
      y_position: Y position [mm].
      z_position: Z position [mm].
      open_gripper_position: Open gripper position [mm].
      z_clearance_height: Z clearance height [mm].
      press_on_distance: Accepted for API compatibility but not forwarded to the
        firmware — the ``zi`` parameter is uncharacterised on real hardware.
      hotel_depth: Hotel depth [mm] (0 = stack).
      minimal_height_at_command_end: Minimal height at command end [mm].
    """
    del press_on_distance
    await self.driver.send_command(
      module="A1RM",
      command="DR",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      zp=round(z_position * 10),
      yo=round(open_gripper_position * 10),
      zc=round(z_clearance_height * 10),
      hd=round(hotel_depth * 10),
      te=round(minimal_height_at_command_end * 10),
    )

  async def move_to_defined_position(
    self,
    x_position: float,
    y_position: float,
    z_position: float,
    minimal_traverse_height_at_begin_of_command: float = 360.0,
  ) -> None:
    """Move to defined position (A1RM:DN).

    Args:
      x_position: X position [mm].
      y_position: Y position [mm].
      z_position: Z position [mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
    """
    await self.driver.send_command(
      module="A1RM",
      command="DN",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      zp=round(z_position * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
    )

  async def expose_channel_n(self) -> None:
    """Expose channel n (A1RM:DQ)."""
    await self.driver.send_command(module="A1RM", command="DQ")

  async def search_for_teach_in_signal_in_x_direction(
    self,
    x_search_distance: float = 0.0,
    x_speed: float = 5.0,
  ) -> None:
    """Search for Teach in signal in X direction (A1RM:DL).

    Args:
      x_search_distance: X search distance [mm].
      x_speed: X speed [mm/s].
    """
    await self.driver.send_command(
      module="A1RM",
      command="DL",
      xs=round(x_search_distance * 10),
      xv=round(x_speed * 10),
    )

  async def set_any_parameter_within_this_module(self) -> None:
    """Set any parameter within this module (A1RM:AA)."""
    await self.driver.send_command(module="A1RM", command="AA")

  async def query_tip_presence(self) -> None:
    """Query tip presence (A1RM:QA)."""
    await self.driver.send_command(module="A1RM", command="QA")

  async def request_access_range(self, grip_orientation: int = 32) -> None:
    """Request access range (A1RM:QR).

    Args:
      grip_orientation: Grip orientation (1-44).
    """
    await self.driver.send_command(module="A1RM", command="QR", gd=grip_orientation)

  async def request_position(self, grip_orientation: int = 32) -> None:
    """Request position (A1RM:QI).

    Args:
      grip_orientation: Grip orientation (1-44).
    """
    await self.driver.send_command(module="A1RM", command="QI", gd=grip_orientation)

  async def request_actual_angular_dimensions(self) -> None:
    """Request actual angular dimensions (A1RM:RR)."""
    await self.driver.send_command(module="A1RM", command="RR")

  async def request_configuration(self) -> None:
    """Request configuration (A1RM:RS)."""
    await self.driver.send_command(module="A1RM", command="RS")
