from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from pylabrobot.capabilities.arms.backend import GripperArmBackend
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate

if TYPE_CHECKING:
  from pylabrobot.hamilton.liquid_handlers.star.driver import STARDriver


class CoreGripper(GripperArmBackend):
  """Backend for Hamilton CoRe gripper tools.

  The CoRe gripper uses two pipetting channels to grip plates along the Y axis.
  Tool management (pick up / return) is handled by the STAR backend.
  """

  def __init__(self, driver: STARDriver):
    self.driver = driver

  # -- lifecycle --------------------------------------------------------------

  async def request_gripper_location(self, backend_params=None) -> GripperLocation:
    raise NotImplementedError("CoreGripper does not support request_gripper_location")

  # -- ArmBackend interface ---------------------------------------------------

  @dataclass
  class PickUpParams(BackendParams):
    """CoRe gripper parameters for plate pickup.

    Args:
      grip_strength: Grip strength (0 = low, 99 = high). Must be between 0 and 99.
        Default 15.
      y_gripping_speed: Y-axis gripping speed in mm/s. Default 5.0.
      z_speed: Z-axis speed in mm/s. Default 50.0.
      minimum_traverse_height: Minimum Z clearance in mm before lateral movement.
        Must be between 0 and 360.0. Default 280.0.
      z_position_at_end: Z position in mm at the end of the command. Must be between
        0 and 360.0. Default 280.0.
    """

    grip_strength: int = 15
    y_gripping_speed: float = 5.0
    z_speed: float = 50.0
    minimum_traverse_height: float = 280.0
    z_position_at_end: float = 280.0

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up a plate at the specified location.

    Args:
      location: Plate center position [mm].
      resource_width: Plate width in Y direction [mm].
      backend_params: CoreGripper.PickUpParams for firmware-specific settings.
    """
    if not isinstance(backend_params, CoreGripper.PickUpParams):
      backend_params = CoreGripper.PickUpParams()

    open_gripper_position = resource_width + 3.0
    plate_width = resource_width - 3.0

    if not 0 <= abs(location.x) <= 3000.0:
      raise ValueError("x_position must be between -3000.0 and 3000.0")
    if not 0 <= abs(location.y) <= 650.0:
      raise ValueError("y_position must be between -650.0 and 650.0")
    if not 0 <= abs(location.z) <= 360.0:
      raise ValueError("z_position must be between -360.0 and 360.0")
    if not 0 <= backend_params.grip_strength <= 99:
      raise ValueError("grip_strength must be between 0 and 99")
    if not 0 <= backend_params.minimum_traverse_height <= 360.0:
      raise ValueError("minimum_traverse_height must be between 0 and 360.0")
    if not 0 <= backend_params.z_position_at_end <= 360.0:
      raise ValueError("z_position_at_end must be between 0 and 360.0")

    await self.driver.send_command(
      module="C0",
      command="ZP",
      xs=f"{abs(round(location.x * 10)):05}",
      xd=int(location.x < 0),
      yj=f"{abs(round(location.y * 10)):04}",
      yv=f"{round(backend_params.y_gripping_speed * 10):04}",
      zj=f"{abs(round(location.z * 10)):04}",
      zy=f"{round(backend_params.z_speed * 10):04}",
      yo=f"{round(open_gripper_position * 10):04}",
      yg=f"{round(plate_width * 10):04}",
      yw=f"{backend_params.grip_strength:02}",
      th=f"{round(backend_params.minimum_traverse_height * 10):04}",
      te=f"{round(backend_params.z_position_at_end * 10):04}",
    )

  @dataclass
  class DropParams(BackendParams):
    """CoRe gripper parameters for plate drop.

    Args:
      z_press_on_distance: Distance in mm to press down on the plate after placing it.
        Default 0.0.
      z_speed: Z-axis speed in mm/s. Default 50.0.
      minimum_traverse_height: Minimum Z clearance in mm before lateral movement.
        Must be between 0 and 360.0. Default 280.0.
      z_position_at_end: Z position in mm at the end of the command. Must be between
        0 and 360.0. Default 280.0.
    """

    z_press_on_distance: float = 0.0
    z_speed: float = 50.0
    minimum_traverse_height: float = 280.0
    z_position_at_end: float = 280.0

  async def drop_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop a plate at the specified location.

    Args:
      location: Plate center position [mm].
      resource_width: Plate width [mm]. Used to compute open gripper position.
      backend_params: CoreGripper.DropParams for firmware-specific settings.
    """
    if not isinstance(backend_params, CoreGripper.DropParams):
      backend_params = CoreGripper.DropParams()

    open_gripper_position = resource_width + 3.0

    if not 0 <= abs(location.x) <= 3000.0:
      raise ValueError("x_position must be between -3000.0 and 3000.0")
    if not 0 <= abs(location.y) <= 650.0:
      raise ValueError("y_position must be between -650.0 and 650.0")
    if not 0 <= abs(location.z) <= 360.0:
      raise ValueError("z_position must be between -360.0 and 360.0")
    if not 0 <= backend_params.minimum_traverse_height <= 360.0:
      raise ValueError("minimum_traverse_height must be between 0 and 360.0")
    if not 0 <= backend_params.z_position_at_end <= 360.0:
      raise ValueError("z_position_at_end must be between 0 and 360.0")

    await self.driver.send_command(
      module="C0",
      command="ZR",
      xs=f"{abs(round(location.x * 10)):05}",
      xd=int(location.x < 0),
      yj=f"{abs(round(location.y * 10)):04}",
      zj=f"{abs(round(location.z * 10)):04}",
      zi=f"{round(backend_params.z_press_on_distance * 10):03}",
      zy=f"{round(backend_params.z_speed * 10):04}",
      yo=f"{round(open_gripper_position * 10):04}",
      th=f"{round(backend_params.minimum_traverse_height * 10):04}",
      te=f"{round(backend_params.z_position_at_end * 10):04}",
    )

  @dataclass
  class MoveToLocationParams(BackendParams):
    """CoRe gripper parameters for moving a held plate to a new position.

    Args:
      acceleration_index: Acceleration index for movement. Must be between 0 and 4.
        Default 4.
      z_speed: Z-axis speed in mm/s. Default 50.0.
      minimum_traverse_height: Minimum Z clearance in mm before lateral movement.
        Must be between 0 and 360.0. Default 280.0.
    """

    acceleration_index: int = 4
    z_speed: float = 50.0
    minimum_traverse_height: float = 280.0

  async def move_to_location(
    self,
    location: Coordinate,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move a held plate to a new position without releasing it.

    Args:
      location: Target plate center position [mm].
      backend_params: CoreGripper.MoveToLocationParams for firmware-specific settings.
    """
    if not isinstance(backend_params, CoreGripper.MoveToLocationParams):
      backend_params = CoreGripper.MoveToLocationParams()

    if not 0 <= abs(location.x) <= 3000.0:
      raise ValueError("x_position must be between -3000.0 and 3000.0")
    if not 0 <= abs(location.y) <= 650.0:
      raise ValueError("y_position must be between -650.0 and 650.0")
    if not 0 <= abs(location.z) <= 360.0:
      raise ValueError("z_position must be between -360.0 and 360.0")
    if not 0 <= backend_params.minimum_traverse_height <= 360.0:
      raise ValueError("minimum_traverse_height must be between 0 and 360.0")

    await self.driver.send_command(
      module="C0",
      command="ZM",
      xs=f"{abs(round(location.x * 10)):05}",
      xd=int(location.x < 0),
      xg=backend_params.acceleration_index,
      yj=f"{abs(round(location.y * 10)):04}",
      zj=f"{abs(round(location.z * 10)):04}",
      zy=f"{round(backend_params.z_speed * 10):04}",
      th=f"{round(backend_params.minimum_traverse_height * 10):04}",
    )

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Open the CoRe gripper."""
    await self.driver.send_command(module="C0", command="ZO")

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    raise NotImplementedError(
      "CoreGripper does not support close_gripper directly. Use pick_up_at_location instead."
    )

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    raise NotImplementedError("CoreGripper does not support is_gripper_closed")

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError("CoreGripper does not support halt")

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError(
      "CoreGripper does not support park. Tool management is handled by the STAR backend."
    )
