"""VantageIPG: Integrated Plate Gripper control for Hamilton Vantage liquid handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from pylabrobot.arms.backend import OrientableGripperArmBackend
from pylabrobot.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate

if TYPE_CHECKING:
  from pylabrobot.hamilton.liquid_handlers.vantage.driver import VantageDriver

logger = logging.getLogger(__name__)


class VantageIPG(OrientableGripperArmBackend):
  """Controls the Integrated Plate Gripper (IPG) on a Hamilton Vantage.

  Implements :class:`OrientableGripperArmBackend`, translating high-level pick/drop
  operations into Vantage firmware commands (module ``A1RM``).

  Args:
    driver: The VantageDriver instance to send commands through.
  """

  def __init__(self, driver: "VantageDriver"):
    self._driver = driver
    self._parked: Optional[bool] = None

  @property
  def parked(self) -> bool:
    return self._parked is True

  # -- CapabilityBackend lifecycle -------------------------------------------

  async def _on_setup(self) -> None:
    """Initialize the IPG if not already initialized, and park if not parked."""
    initialized = await self.ipg_request_initialization_status()
    if not initialized:
      await self.ipg_initialize()
    parked = await self.ipg_get_parking_status()
    if not parked:
      await self.ipg_park()
    self._parked = True

  # -- OrientableGripperArmBackend abstract methods --------------------------

  @dataclass
  class PickUpParams(BackendParams):
    grip_strength: int = 81
    plate_width_tolerance: int = 20
    acceleration_index: int = 4
    z_clearance_height: int = 0
    hotel_depth: int = 0
    minimal_height_at_command_end: int = 2840

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up a plate at the specified location using the IPG.

    Args:
      location: Plate center position [mm].
      direction: Grip direction in degrees (unused — IPG grip orientation is set separately via
        :meth:`ipg_prepare_gripper_orientation`).
      resource_width: Plate width [mm].
      backend_params: VantageIPG.PickUpParams for firmware-specific settings.
    """
    if not isinstance(backend_params, VantageIPG.PickUpParams):
      backend_params = VantageIPG.PickUpParams()

    open_gripper_position = round(resource_width * 10) + 32
    plate_width = round(resource_width * 10) - 33

    await self.ipg_grip_plate(
      x_position=round(location.x * 10),
      y_position=round(location.y * 10),
      z_position=round(location.z * 10),
      grip_strength=backend_params.grip_strength,
      open_gripper_position=open_gripper_position,
      plate_width=plate_width,
      plate_width_tolerance=backend_params.plate_width_tolerance,
      acceleration_index=backend_params.acceleration_index,
      z_clearance_height=backend_params.z_clearance_height,
      hotel_depth=backend_params.hotel_depth,
      minimal_height_at_command_end=backend_params.minimal_height_at_command_end,
    )
    self._parked = False

  @dataclass
  class DropParams(BackendParams):
    z_clearance_height: int = 0
    press_on_distance: int = 5
    hotel_depth: int = 0
    minimal_height_at_command_end: int = 2840

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop a plate at the specified location using the IPG.

    Args:
      location: Plate center position [mm].
      direction: Grip direction in degrees (unused — IPG grip orientation is set separately via
        :meth:`ipg_prepare_gripper_orientation`).
      resource_width: Plate width [mm]. Used to compute open gripper position.
      backend_params: VantageIPG.DropParams for firmware-specific settings.
    """
    if not isinstance(backend_params, VantageIPG.DropParams):
      backend_params = VantageIPG.DropParams()

    open_gripper_position = round(resource_width * 10) + 32

    await self.ipg_put_plate(
      x_position=round(location.x * 10),
      y_position=round(location.y * 10),
      z_position=round(location.z * 10),
      open_gripper_position=open_gripper_position,
      z_clearance_height=backend_params.z_clearance_height,
      press_on_distance=backend_params.press_on_distance,
      hotel_depth=backend_params.hotel_depth,
      minimal_height_at_command_end=backend_params.minimal_height_at_command_end,
    )
    self._parked = False

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move the IPG (with a held plate) to a defined position.

    Args:
      location: Target position [mm].
      direction: Grip direction in degrees (unused for IPG).
      backend_params: Unused, reserved for future use.
    """
    await self.ipg_move_to_defined_position(
      x_position=round(location.x * 10),
      y_position=round(location.y * 10),
      z_position=round(location.z * 10),
    )
    self._parked = False

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Park the IPG."""
    await self.ipg_park()
    self._parked = True

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Open the IPG gripper (release object).

    Args:
      gripper_width: Unused for IPG — the gripper simply releases.
      backend_params: Unused, reserved for future use.
    """
    await self.ipg_release_object()

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Close the IPG gripper.

    For the IPG, closing the gripper is done as part of :meth:`pick_up_at_location` via
    :meth:`ipg_grip_plate`. This method raises :class:`NotImplementedError` because
    standalone close is not supported by the IPG firmware.

    Args:
      gripper_width: Plate width [mm].
      backend_params: Unused, reserved for future use.
    """
    raise NotImplementedError(
      "IPG does not support standalone close_gripper. Use pick_up_at_location instead."
    )

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    """Check if the IPG is holding a plate.

    Returns:
      True if the IPG is not parked (i.e. presumably holding a plate), False otherwise.
    """
    return not await self.ipg_get_parking_status()

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    raise NotImplementedError("IPG does not support request_gripper_location")

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError("IPG halt not yet implemented")

  # -- Raw firmware protocol methods -----------------------------------------

  async def ipg_request_initialization_status(self) -> bool:
    """Request initialization status of IPG.

    This command was based on the STAR command (QW) and the VStarTranslator log. A1RM corresponds
    to "arm".

    Returns:
      True if the IPG module is initialized, False otherwise.
    """
    resp = await self._driver.send_command(module="A1RM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def ipg_initialize(self):
    """Initialize IPG."""
    return await self._driver.send_command(module="A1RM", command="DI")

  async def ipg_park(self):
    """Park IPG."""
    return await self._driver.send_command(module="A1RM", command="GP")

  async def ipg_expose_channel_n(self):
    """Expose channel n."""
    return await self._driver.send_command(module="A1RM", command="DQ")

  async def ipg_release_object(self):
    """Release object."""
    return await self._driver.send_command(module="A1RM", command="DO")

  async def ipg_search_for_teach_in_signal_in_x_direction(
    self,
    x_search_distance: int = 0,
    x_speed: int = 50,
  ):
    """Search for Teach in signal in X direction.

    Args:
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
    """
    if not -50000 <= x_search_distance <= 50000:
      raise ValueError("x_search_distance must be in range -50000 to 50000")
    if not 20 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 20 to 25000")

    return await self._driver.send_command(
      module="A1RM",
      command="DL",
      xs=x_search_distance,
      xv=x_speed,
    )

  async def ipg_grip_plate(
    self,
    x_position: int = 5000,
    y_position: int = 5600,
    z_position: int = 3600,
    grip_strength: int = 100,
    open_gripper_position: int = 860,
    plate_width: int = 800,
    plate_width_tolerance: int = 20,
    acceleration_index: int = 4,
    z_clearance_height: int = 50,
    hotel_depth: int = 0,
    minimal_height_at_command_end: int = 3600,
  ):
    """Grip plate.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      z_position: Z Position [0.1mm].
      grip_strength: Grip strength (0 = low 99 = high).
      open_gripper_position: Open gripper position [0.1mm].
      plate_width: Plate width [0.1mm].
      plate_width_tolerance: Plate width tolerance [0.1mm].
      acceleration_index: Acceleration index.
      z_clearance_height: Z clearance height [0.1mm].
      hotel_depth: Hotel depth [0.1mm] (0 = Stack).
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """
    if not -50000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -50000 to 50000")
    if not -10000 <= y_position <= 10000:
      raise ValueError("y_position must be in range -10000 to 10000")
    if not 0 <= z_position <= 4000:
      raise ValueError("z_position must be in range 0 to 4000")
    if not 0 <= grip_strength <= 160:
      raise ValueError("grip_strength must be in range 0 to 160")
    if not 0 <= open_gripper_position <= 9999:
      raise ValueError("open_gripper_position must be in range 0 to 9999")
    if not 0 <= plate_width <= 9999:
      raise ValueError("plate_width must be in range 0 to 9999")
    if not 0 <= plate_width_tolerance <= 99:
      raise ValueError("plate_width_tolerance must be in range 0 to 99")
    if not 0 <= acceleration_index <= 4:
      raise ValueError("acceleration_index must be in range 0 to 4")
    if not 0 <= z_clearance_height <= 999:
      raise ValueError("z_clearance_height must be in range 0 to 999")
    if not 0 <= hotel_depth <= 3000:
      raise ValueError("hotel_depth must be in range 0 to 3000")
    if not 0 <= minimal_height_at_command_end <= 4000:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 4000")

    return await self._driver.send_command(
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

  async def ipg_put_plate(
    self,
    x_position: int = 5000,
    y_position: int = 5600,
    z_position: int = 3600,
    open_gripper_position: int = 860,
    z_clearance_height: int = 50,
    press_on_distance: int = 5,
    hotel_depth: int = 0,
    minimal_height_at_command_end: int = 3600,
  ):
    """Put plate.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      z_position: Z Position [0.1mm].
      open_gripper_position: Open gripper position [0.1mm].
      z_clearance_height: Z clearance height [0.1mm].
      press_on_distance: Press on distance [0.1mm].
      hotel_depth: Hotel depth [0.1mm] (0 = Stack).
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """
    if not -50000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -50000 to 50000")
    if not -10000 <= y_position <= 10000:
      raise ValueError("y_position must be in range -10000 to 10000")
    if not 0 <= z_position <= 4000:
      raise ValueError("z_position must be in range 0 to 4000")
    if not 0 <= open_gripper_position <= 9999:
      raise ValueError("open_gripper_position must be in range 0 to 9999")
    if not 0 <= z_clearance_height <= 999:
      raise ValueError("z_clearance_height must be in range 0 to 999")
    if not 0 <= press_on_distance <= 999:
      raise ValueError("press_on_distance must be in range 0 to 999")
    if not 0 <= hotel_depth <= 3000:
      raise ValueError("hotel_depth must be in range 0 to 3000")
    if not 0 <= minimal_height_at_command_end <= 4000:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 4000")

    return await self._driver.send_command(
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

  async def ipg_prepare_gripper_orientation(
    self,
    grip_orientation: int = 32,
    minimal_traverse_height_at_begin_of_command: int = 3600,
  ):
    """Prepare gripper orientation.

    Args:
      grip_orientation: Grip orientation.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
        command [0.1mm].
    """
    if not 1 <= grip_orientation <= 44:
      raise ValueError("grip_orientation must be in range 1 to 44")
    if not 0 <= minimal_traverse_height_at_begin_of_command <= 4000:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 4000")

    return await self._driver.send_command(
      module="A1RM",
      command="GA",
      gd=grip_orientation,
      th=minimal_traverse_height_at_begin_of_command,
    )

  async def ipg_move_to_defined_position(
    self,
    x_position: int = 5000,
    y_position: int = 5600,
    z_position: int = 3600,
    minimal_traverse_height_at_begin_of_command: int = 3600,
  ):
    """Move to defined position.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      z_position: Z Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
        command [0.1mm].
    """
    if not -50000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -50000 to 50000")
    if not -10000 <= y_position <= 10000:
      raise ValueError("y_position must be in range -10000 to 10000")
    if not 0 <= z_position <= 4000:
      raise ValueError("z_position must be in range 0 to 4000")
    if not 0 <= minimal_traverse_height_at_begin_of_command <= 4000:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 4000")

    return await self._driver.send_command(
      module="A1RM",
      command="DN",
      xp=x_position,
      yp=y_position,
      zp=z_position,
      th=minimal_traverse_height_at_begin_of_command,
    )

  async def ipg_set_any_parameter_within_this_module(self):
    """Set any parameter within this module."""
    return await self._driver.send_command(module="A1RM", command="AA")

  async def ipg_get_parking_status(self) -> bool:
    """Get parking status.

    Returns:
      True if parked, False otherwise.
    """
    resp = await self._driver.send_command(module="A1RM", command="RG", fmt={"rg": "int"})
    return resp is not None and resp["rg"] == 1

  async def ipg_query_tip_presence(self):
    """Query tip presence."""
    return await self._driver.send_command(module="A1RM", command="QA")

  async def ipg_request_access_range(self, grip_orientation: int = 32):
    """Request access range.

    Args:
      grip_orientation: Grip orientation.
    """
    if not 1 <= grip_orientation <= 44:
      raise ValueError("grip_orientation must be in range 1 to 44")

    return await self._driver.send_command(
      module="A1RM",
      command="QR",
      gd=grip_orientation,
    )

  async def ipg_request_position(self, grip_orientation: int = 32):
    """Request position.

    Args:
      grip_orientation: Grip orientation.
    """
    if not 1 <= grip_orientation <= 44:
      raise ValueError("grip_orientation must be in range 1 to 44")

    return await self._driver.send_command(
      module="A1RM",
      command="QI",
      gd=grip_orientation,
    )

  async def ipg_request_actual_angular_dimensions(self):
    """Request actual angular dimensions."""
    return await self._driver.send_command(module="A1RM", command="RR")

  async def ipg_request_configuration(self):
    """Request configuration."""
    return await self._driver.send_command(module="A1RM", command="RS")
