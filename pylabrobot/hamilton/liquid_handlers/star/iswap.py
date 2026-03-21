from dataclasses import dataclass
from typing import Optional, cast

from pylabrobot.arms.backend import OrientableArmBackend
from pylabrobot.legacy.liquid_handling.backends.hamilton.base import HamiltonLiquidHandler
from pylabrobot.resources import Coordinate
from pylabrobot.serializer import SerializableMixin


def _direction_degrees_to_grip_direction(degrees: float) -> int:
  """Convert rotation angle in degrees to firmware grip_direction (1-4).

  Firmware:  1 = negative Y (front), 2 = positive X (right),
             3 = positive Y (back),  4 = negative X (left).
  """
  normalized = round(degrees) % 360
  mapping = {0: 1, 90: 2, 180: 3, 270: 4}
  if normalized not in mapping:
    raise ValueError(f"grip direction must be a multiple of 90 degrees, got {degrees}")
  return mapping[normalized]


class iSWAP(OrientableArmBackend):
  def __init__(self, interface: HamiltonLiquidHandler):
    self.interface = interface
    self._version: Optional[str] = None
    self._parked: Optional[bool] = None

  @property
  def version(self) -> str:
    """Firmware version string. Available after setup."""
    if self._version is None:
      raise RuntimeError("iSWAP version not loaded. Call setup() first.")
    return self._version

  @property
  def parked(self) -> bool:
    return self._parked is True

  async def setup(self) -> None:
    self._version = await self._request_version()

  async def _request_version(self) -> str:
    """Request the iSWAP firmware version from the device."""
    return cast(
      str, (await self.interface.send_command("R0", "RF", fmt="rf" + "&" * 15))["rf"]
    )

  @dataclass
  class ParkParams(SerializableMixin):
    minimum_traverse_height: float = 284.0

  async def park(self, backend_params: Optional[SerializableMixin] = None) -> None:
    """Park the iSWAP.

    Args:
      backend_params: iSWAP.ParkParams with minimum_traverse_height.
    """
    if not isinstance(backend_params, iSWAP.ParkParams):
      backend_params = iSWAP.ParkParams()

    if not 0 <= backend_params.minimum_traverse_height <= 360.0:
      raise ValueError("minimum_traverse_height must be between 0 and 360.0")

    await self.interface.send_command(
      module="C0",
      command="PG",
      th=round(backend_params.minimum_traverse_height * 10),
    )
    self._parked = True

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    """Open the iSWAP gripper.

    Args:
      gripper_width: Open position [mm].
      backend_params: Unused, reserved for future use.
    """
    if not 0 <= gripper_width <= 999.9:
      raise ValueError("gripper_width must be between 0 and 999.9")

    await self.interface.send_command(
      module="C0", command="GF", go=f"{round(gripper_width * 10):04}"
    )

  @dataclass
  class CloseGripperParams(SerializableMixin):
    grip_strength: int = 5
    plate_width_tolerance: float = 0

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    """Close the iSWAP gripper.

    Args:
      gripper_width: Plate width [mm].
      backend_params: iSWAP.CloseGripperParams with grip_strength and plate_width_tolerance.
    """
    if not isinstance(backend_params, iSWAP.CloseGripperParams):
      backend_params = iSWAP.CloseGripperParams()

    if not 0 <= backend_params.grip_strength <= 9:
      raise ValueError("grip_strength must be between 0 and 9")
    if not 0 <= gripper_width <= 999.9:
      raise ValueError("gripper_width must be between 0 and 999.9")
    if not 0 <= backend_params.plate_width_tolerance <= 9.9:
      raise ValueError("plate_width_tolerance must be between 0 and 9.9")

    await self.interface.send_command(
      module="C0",
      command="GC",
      gw=backend_params.grip_strength,
      gb=f"{round(gripper_width * 10):04}",
      gt=f"{round(backend_params.plate_width_tolerance * 10):02}",
    )

  @dataclass
  class PickUpParams(SerializableMixin):
    minimum_traverse_height: float = 280.0
    z_position_at_end: float = 280.0
    grip_strength: int = 4
    plate_width_tolerance: float = 2.0
    collision_control_level: int = 0
    acceleration_index_high_acc: int = 4
    acceleration_index_low_acc: int = 1
    fold_up_at_end: bool = False

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Pick up a plate at the specified location.

    Args:
      location: Plate center position [mm].
      direction: Grip direction in degrees (0=front, 90=right, 180=back, 270=left).
      resource_width: Plate width [mm].
      backend_params: iSWAP.PickUpParams for firmware-specific settings.
    """
    if not isinstance(backend_params, iSWAP.PickUpParams):
      backend_params = iSWAP.PickUpParams()

    open_gripper_position = resource_width + 3.0
    plate_width_for_firmware = round(resource_width * 10) - 33

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
    if not 1 <= backend_params.grip_strength <= 9:
      raise ValueError("grip_strength must be between 1 and 9")
    if not 0 <= open_gripper_position <= 999.9:
      raise ValueError("open_gripper_position must be between 0 and 999.9")
    if not 0 <= resource_width <= 999.9:
      raise ValueError("resource_width must be between 0 and 999.9")
    if not 0 <= backend_params.plate_width_tolerance <= 9.9:
      raise ValueError("plate_width_tolerance must be between 0 and 9.9")
    if not 0 <= backend_params.collision_control_level <= 1:
      raise ValueError("collision_control_level must be 0 or 1")
    if not 0 <= backend_params.acceleration_index_high_acc <= 4:
      raise ValueError("acceleration_index_high_acc must be between 0 and 4")
    if not 0 <= backend_params.acceleration_index_low_acc <= 4:
      raise ValueError("acceleration_index_low_acc must be between 0 and 4")

    grip_dir = _direction_degrees_to_grip_direction(direction)

    await self.interface.send_command(
      module="C0",
      command="PP",
      xs=f"{abs(round(location.x * 10)):05}",
      xd=int(location.x < 0),
      yj=f"{abs(round(location.y * 10)):04}",
      yd=int(location.y < 0),
      zj=f"{abs(round(location.z * 10)):04}",
      zd=int(location.z < 0),
      gr=grip_dir,
      th=f"{round(backend_params.minimum_traverse_height * 10):04}",
      te=f"{round(backend_params.z_position_at_end * 10):04}",
      gw=backend_params.grip_strength,
      go=f"{round(open_gripper_position * 10):04}",
      gb=f"{plate_width_for_firmware:04}",
      gt=f"{round(backend_params.plate_width_tolerance * 10):02}",
      ga=backend_params.collision_control_level,
      gc=backend_params.fold_up_at_end,
    )
    self._parked = False

  @dataclass
  class DropParams(SerializableMixin):
    minimum_traverse_height: float = 280.0
    z_position_at_end: float = 280.0
    collision_control_level: int = 0
    acceleration_index_high_acc: int = 4
    acceleration_index_low_acc: int = 1
    fold_up_at_end: bool = False

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Drop a plate at the specified location.

    Args:
      location: Plate center position [mm].
      direction: Grip direction in degrees (0=front, 90=right, 180=back, 270=left).
      resource_width: Plate width [mm]. Used to compute open gripper position.
      backend_params: iSWAP.DropParams for firmware-specific settings.
    """
    if not isinstance(backend_params, iSWAP.DropParams):
      backend_params = iSWAP.DropParams()

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
    if not 0 <= open_gripper_position <= 999.9:
      raise ValueError("open_gripper_position must be between 0 and 999.9")
    if not 0 <= backend_params.collision_control_level <= 1:
      raise ValueError("collision_control_level must be 0 or 1")
    if not 0 <= backend_params.acceleration_index_high_acc <= 4:
      raise ValueError("acceleration_index_high_acc must be between 0 and 4")
    if not 0 <= backend_params.acceleration_index_low_acc <= 4:
      raise ValueError("acceleration_index_low_acc must be between 0 and 4")

    grip_dir = _direction_degrees_to_grip_direction(direction)

    await self.interface.send_command(
      module="C0",
      command="PR",
      xs=f"{abs(round(location.x * 10)):05}",
      xd=int(location.x < 0),
      yj=f"{abs(round(location.y * 10)):04}",
      yd=int(location.y < 0),
      zj=f"{abs(round(location.z * 10)):04}",
      zd=int(location.z < 0),
      th=f"{round(backend_params.minimum_traverse_height * 10):04}",
      te=f"{round(backend_params.z_position_at_end * 10):04}",
      gr=grip_dir,
      go=f"{round(open_gripper_position * 10):04}",
      ga=backend_params.collision_control_level,
      gc=backend_params.fold_up_at_end,
    )
    self._parked = False

  async def is_gripper_closed(self) -> bool:
    """Check if the iSWAP is holding a plate.

    Returns:
      True if holding a plate, False otherwise.
    """
    resp = await self.interface.send_command(module="C0", command="QP", fmt="ph#")
    return resp is not None and resp["ph"] == 1

  async def stop(self) -> None:
    raise NotImplementedError()

  @dataclass
  class MoveToLocationParams(SerializableMixin):
    minimum_traverse_height: float = 360.0
    collision_control_level: int = 1
    acceleration_index_high_acc: int = 4
    acceleration_index_low_acc: int = 1

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Move a held plate to a new position without releasing it.

    Args:
      location: Target plate center position [mm].
      direction: Grip direction in degrees (0=front, 90=right, 180=back, 270=left).
      backend_params: iSWAP.MoveToLocationParams for firmware-specific settings.
    """
    if not isinstance(backend_params, iSWAP.MoveToLocationParams):
      backend_params = iSWAP.MoveToLocationParams()

    if not 0 <= abs(location.x) <= 3000.0:
      raise ValueError("x_position must be between -3000.0 and 3000.0")
    if not 0 <= abs(location.y) <= 650.0:
      raise ValueError("y_position must be between -650.0 and 650.0")
    if not 0 <= abs(location.z) <= 360.0:
      raise ValueError("z_position must be between -360.0 and 360.0")
    if not 0 <= backend_params.minimum_traverse_height <= 360.0:
      raise ValueError("minimum_traverse_height must be between 0 and 360.0")
    if not 0 <= backend_params.collision_control_level <= 1:
      raise ValueError("collision_control_level must be 0 or 1")
    if not 0 <= backend_params.acceleration_index_high_acc <= 4:
      raise ValueError("acceleration_index_high_acc must be between 0 and 4")
    if not 0 <= backend_params.acceleration_index_low_acc <= 4:
      raise ValueError("acceleration_index_low_acc must be between 0 and 4")

    grip_dir = _direction_degrees_to_grip_direction(direction)

    await self.interface.send_command(
      module="C0",
      command="PM",
      xs=f"{abs(round(location.x * 10)):05}",
      xd=int(location.x < 0),
      yj=f"{abs(round(location.y * 10)):04}",
      yd=int(location.y < 0),
      zj=f"{abs(round(location.z * 10)):04}",
      zd=int(location.z < 0),
      gr=grip_dir,
      th=f"{round(backend_params.minimum_traverse_height * 10):04}",
      ga=backend_params.collision_control_level,
      xe=f"{backend_params.acceleration_index_high_acc} {backend_params.acceleration_index_low_acc}",
    )
    self._parked = False

  async def halt(self) -> None:
    raise NotImplementedError()

  async def move_to_safe(self) -> None:
    raise NotImplementedError()
