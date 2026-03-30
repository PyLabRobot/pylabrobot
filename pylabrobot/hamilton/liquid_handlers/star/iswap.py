from __future__ import annotations

import enum
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional, cast

from pylabrobot.arms.backend import OrientableGripperArmBackend
from pylabrobot.arms.standard import GripDirection, GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate
from pylabrobot.resources.rotation import Rotation

if TYPE_CHECKING:
  from pylabrobot.hamilton.liquid_handlers.star.driver import STARDriver


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


class iSWAPBackend(OrientableGripperArmBackend):

  class RotationDriveOrientation(enum.Enum):
    LEFT = 1
    FRONT = 2
    RIGHT = 3
    PARKED_RIGHT = None

  class WristDriveOrientation(enum.Enum):
    RIGHT = 1
    STRAIGHT = 2
    LEFT = 3
    REVERSE = 4

  def __init__(self, driver: STARDriver):
    self.driver = driver
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

  async def request_gripper_location(self, backend_params=None) -> GripperLocation:
    """Request iSWAP grip center position (C0 QG).

    Returns:
      GripperLocation with position in mm and a default rotation.
    """
    resp = await self.driver.send_command(
      module="C0", command="QG", fmt="xs#####xd#yj####yd#zj####zd#"
    )
    location = Coordinate(
      x=(resp["xs"] / 10) * (1 if resp["xd"] == 0 else -1),
      y=(resp["yj"] / 10) * (1 if resp["yd"] == 0 else -1),
      z=(resp["zj"] / 10) * (1 if resp["zd"] == 0 else -1),
    )
    return GripperLocation(location=location, rotation=Rotation())

  async def _on_setup(self) -> None:
    if self._version is None:
      self._version = await self._request_version()

  async def _request_version(self) -> str:
    """Request the iSWAP firmware version from the device."""
    return cast(str, (await self.driver.send_command("R0", "RF", fmt="rf" + "&" * 15))["rf"])

  async def initialize(self) -> None:
    """Initialize iSWAP (C0 FI). For standalone configuration only."""
    await self.driver.send_command(module="C0", command="FI")

  async def open_not_initialized_gripper(self) -> None:
    """Open gripper when iSWAP is not yet initialized (C0 GI)."""
    await self.driver.send_command(module="C0", command="GI")

  async def dangerous_release_brake(self) -> None:
    """Release the iSWAP brake (R0 BA). Use with caution."""
    await self.driver.send_command(module="R0", command="BA")

  async def reengage_brake(self) -> None:
    """Re-engage the iSWAP brake (R0 BO)."""
    await self.driver.send_command(module="R0", command="BO")

  async def initialize_z_axis(self) -> None:
    """Initialize the iSWAP Z axis (R0 ZI)."""
    await self.driver.send_command(module="R0", command="ZI")

  # -- relative / absolute movement ------------------------------------------

  async def move_x_relative(self, step_size: float, allow_splitting: bool = False) -> None:
    """Move iSWAP X by a relative step (C0 GX).

    Args:
      step_size: X step size [mm]. Between -99.9 and 99.9 unless allow_splitting is True.
      allow_splitting: Allow splitting into multiple firmware commands.
    """
    direction = 0 if step_size >= 0 else 1
    max_step = 99.9
    if abs(step_size) > max_step:
      if not allow_splitting:
        raise ValueError("step_size must be between -99.9 and 99.9")
      first = max_step if step_size > 0 else -max_step
      await self.move_x_relative(step_size=first, allow_splitting=True)
      remaining = step_size - first
      return await self.move_x_relative(remaining, allow_splitting=True)

    await self.driver.send_command(
      module="C0", command="GX",
      gx=f"{round(abs(step_size) * 10):03}",
      xd=direction,
    )

  async def move_y_relative(self, step_size: float, allow_splitting: bool = False) -> None:
    """Move iSWAP Y by a relative step (C0 GY).

    Args:
      step_size: Y step size [mm]. Between -99.9 and 99.9 unless allow_splitting is True.
      allow_splitting: Allow splitting into multiple firmware commands.
    """
    direction = 0 if step_size >= 0 else 1
    max_step = 99.9
    if abs(step_size) > max_step:
      if not allow_splitting:
        raise ValueError("step_size must be between -99.9 and 99.9")
      first = max_step if step_size > 0 else -max_step
      await self.move_y_relative(step_size=first, allow_splitting=True)
      remaining = step_size - first
      return await self.move_y_relative(remaining, allow_splitting=True)

    await self.driver.send_command(
      module="C0", command="GY",
      gy=f"{round(abs(step_size) * 10):03}",
      yd=direction,
    )

  async def move_z_relative(self, step_size: float, allow_splitting: bool = False) -> None:
    """Move iSWAP Z by a relative step (C0 GZ).

    Args:
      step_size: Z step size [mm]. Between -99.9 and 99.9 unless allow_splitting is True.
      allow_splitting: Allow splitting into multiple firmware commands.
    """
    direction = 0 if step_size >= 0 else 1
    max_step = 99.9
    if abs(step_size) > max_step:
      if not allow_splitting:
        raise ValueError("step_size must be between -99.9 and 99.9")
      first = max_step if step_size > 0 else -max_step
      await self.move_z_relative(step_size=first, allow_splitting=True)
      remaining = step_size - first
      return await self.move_z_relative(remaining, allow_splitting=True)

    await self.driver.send_command(
      module="C0", command="GZ",
      gz=f"{round(abs(step_size) * 10):03}",
      zd=direction,
    )

  async def request_in_parking_position(self) -> dict:
    """Request iSWAP parking position status (C0 RG).

    Returns:
      Parsed response dict with key ``"rg"`` (0 = not parked, 1 = parked).
    """
    return await self.driver.send_command(module="C0", command="RG", fmt="rg#")

  async def request_initialization_status(self) -> bool:
    """Request iSWAP initialization status (R0 QW).

    Returns:
      True if iSWAP is fully initialized.
    """
    resp = await self.driver.send_command(module="R0", command="QW", fmt="qw#")
    return cast(int, resp["qw"]) == 1

  async def rotation_drive_request_y(self) -> float:
    """Request iSWAP rotation drive Y position (center) in mm (R0 RY).

    This is equivalent to the Y location of the iSWAP module.
    """
    if not self.driver.extended_conf.left_x_drive.iswap_installed:
      raise RuntimeError("iSWAP is not installed")
    resp = await self.driver.send_command(module="R0", command="RY", fmt="ry##### (n)")
    iswap_y_pos = resp["ry"][1]  # 0 = FW counter, 1 = HW counter
    return round(self.driver.y_drive_increment_to_mm(iswap_y_pos), 1)

  async def move_x(self, x_position: float) -> None:
    """Move iSWAP X to an absolute position [mm]."""
    loc = (await self.get_gripper_location()).location
    await self.move_x_relative(step_size=x_position - loc.x, allow_splitting=True)

  async def move_y(self, y_position: float) -> None:
    """Move iSWAP Y to an absolute position [mm]."""
    loc = (await self.get_gripper_location()).location
    await self.move_y_relative(step_size=y_position - loc.y, allow_splitting=True)

  async def move_z(self, z_position: float) -> None:
    """Move iSWAP Z to an absolute position [mm]."""
    loc = (await self.get_gripper_location()).location
    await self.move_z_relative(step_size=z_position - loc.z, allow_splitting=True)

  # -- rotation / wrist drive ------------------------------------------------

  async def request_rotation_drive_position_increments(self) -> int:
    """Query the iSWAP rotation drive position in increments (R0 RW)."""
    response = await self.driver.send_command(module="R0", command="RW", fmt="rw######")
    return cast(int, response["rw"])

  async def request_rotation_drive_orientation(self) -> "iSWAPBackend.RotationDriveOrientation":
    """Request the iSWAP rotation drive orientation.

    Uses empirically determined increment values:
      FRONT: -25 +/- 50, RIGHT: +29068 +/- 50, LEFT: -29116 +/- 50
    """
    RDO = iSWAPBackend.RotationDriveOrientation
    rotation_orientation_to_motor_increment_dict = {
      RDO.FRONT: range(-75, 26),
      RDO.RIGHT: range(29018, 29119),
      RDO.LEFT: range(-29166, -29065),
      RDO.PARKED_RIGHT: range(29450, 29550),
    }

    motor_position_increments = await self.request_rotation_drive_position_increments()

    for orientation, increment_range in rotation_orientation_to_motor_increment_dict.items():
      if motor_position_increments in increment_range:
        return orientation

    raise ValueError(
      f"Unknown rotation orientation: {motor_position_increments}. "
      f"Expected one of {list(rotation_orientation_to_motor_increment_dict.values())}."
    )

  async def request_wrist_drive_position_increments(self) -> int:
    """Query the iSWAP wrist drive position in increments (R0 RT)."""
    response = await self.driver.send_command(module="R0", command="RT", fmt="rt######")
    return cast(int, response["rt"])

  async def request_wrist_drive_orientation(self) -> "iSWAPBackend.WristDriveOrientation":
    """Request the iSWAP wrist drive orientation.

    The wrist orientation is relative to the rotation drive orientation.
    """
    WDO = iSWAPBackend.WristDriveOrientation
    wrist_orientation_to_motor_increment_dict = {
      WDO.RIGHT: range(-26_627, -26_527),
      WDO.STRAIGHT: range(-8_804, -8_704),
      WDO.LEFT: range(9_051, 9_151),
      WDO.REVERSE: range(26_802, 26_902),
    }

    motor_position_increments = await self.request_wrist_drive_position_increments()

    for orientation, increment_range in wrist_orientation_to_motor_increment_dict.items():
      if motor_position_increments in increment_range:
        return orientation

    raise ValueError(
      f"Unknown wrist orientation: {motor_position_increments}. "
      f"Expected one of {list(wrist_orientation_to_motor_increment_dict)}."
    )

  async def rotate(
    self,
    rotation_drive: "iSWAPBackend.RotationDriveOrientation",
    grip_direction: GripDirection,
    gripper_velocity: int = 55_000,
    gripper_acceleration: int = 170,
    gripper_protection: Literal[0, 1, 2, 3, 4, 5, 6, 7] = 5,
    wrist_velocity: int = 48_000,
    wrist_acceleration: int = 145,
    wrist_protection: Literal[0, 1, 2, 3, 4, 5, 6, 7] = 5,
  ) -> None:
    """Rotate the iSWAP to a predefined position (R0 PD).

    Velocity units are incr/sec. Acceleration units are 1000 incr/sec^2.
    """
    assert 20 <= gripper_velocity <= 75_000
    assert 5 <= gripper_acceleration <= 200
    assert 20 <= wrist_velocity <= 65_000
    assert 20 <= wrist_acceleration <= 200

    RDO = iSWAPBackend.RotationDriveOrientation
    position = 0

    if rotation_drive.value == RDO.LEFT.value:
      position += 10
    elif rotation_drive.value == RDO.FRONT.value:
      position += 20
    elif rotation_drive.value == RDO.RIGHT.value:
      position += 30
    else:
      raise ValueError(f"Invalid rotation drive orientation: {rotation_drive}")

    if grip_direction.value == GripDirection.FRONT.value:
      position += 1
    elif grip_direction.value == GripDirection.RIGHT.value:
      position += 2
    elif grip_direction.value == GripDirection.BACK.value:
      position += 3
    elif grip_direction.value == GripDirection.LEFT.value:
      position += 4
    else:
      raise ValueError("Invalid grip direction")

    await self.driver.send_command(
      module="R0",
      command="PD",
      pd=position,
      wv=f"{gripper_velocity:05}",
      wr=f"{gripper_acceleration:03}",
      ww=gripper_protection,
      tv=f"{wrist_velocity:05}",
      tr=f"{wrist_acceleration:03}",
      tw=wrist_protection,
    )

  async def rotate_rotation_drive(
    self, orientation: "iSWAPBackend.RotationDriveOrientation"
  ) -> None:
    """Rotate the rotation drive to the given orientation (R0 WP)."""
    RDO = iSWAPBackend.RotationDriveOrientation
    if orientation.value not in {RDO.RIGHT.value, RDO.FRONT.value, RDO.LEFT.value}:
      raise ValueError(f"Invalid rotation drive orientation: {orientation}")
    await self.driver.send_command(
      module="R0", command="WP", auto_id=False, wp=orientation.value,
    )

  async def rotate_wrist(self, orientation: "iSWAPBackend.WristDriveOrientation") -> None:
    """Rotate the wrist to the given orientation (R0 TP)."""
    await self.driver.send_command(
      module="R0", command="TP", auto_id=False, tp=orientation.value,
    )

  # -- collapse, teaching, velocity control ----------------------------------

  async def collapse_gripper_arm(
    self,
    minimum_traverse_height: float = 360.0,
    fold_up_at_end: bool = False,
  ) -> None:
    """Collapse / fold the gripper arm (C0 PN).

    Args:
      minimum_traverse_height: Minimum traverse height [mm]. 0..360.
      fold_up_at_end: Fold-up sequence at end of process.
    """
    if not 0 <= minimum_traverse_height <= 360.0:
      raise ValueError("minimum_traverse_height must be between 0 and 360.0")

    await self.driver.send_command(
      module="C0",
      command="PN",
      th=round(minimum_traverse_height * 10),
      gc=fold_up_at_end,
    )

  async def prepare_teaching(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    minimum_traverse_height: int = 3600,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ) -> None:
    """Prepare for teaching with iSWAP (C0 PT).

    All position args are in 0.1mm firmware units.
    """
    assert 0 <= x_position <= 30000
    assert 0 <= x_direction <= 1
    assert 0 <= y_position <= 6500
    assert 0 <= y_direction <= 1
    assert 0 <= z_position <= 3600
    assert 0 <= z_direction <= 1
    assert 0 <= location <= 1
    assert 0 <= hotel_depth <= 3000
    assert 0 <= minimum_traverse_height <= 3600
    assert 0 <= collision_control_level <= 1
    assert 0 <= acceleration_index_high_acc <= 4
    assert 0 <= acceleration_index_low_acc <= 4

    await self.driver.send_command(
      module="C0",
      command="PT",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      hh=location,
      hd=f"{hotel_depth:04}",
      gr=grip_direction,
      th=f"{minimum_traverse_height:04}",
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
    )

  async def get_logic_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    collision_control_level: int = 1,
  ) -> None:
    """Get logic iSWAP position (C0 PC).

    All position args are in 0.1mm firmware units.
    """
    assert 0 <= x_position <= 30000
    assert 0 <= x_direction <= 1
    assert 0 <= y_position <= 6500
    assert 0 <= y_direction <= 1
    assert 0 <= z_position <= 3600
    assert 0 <= z_direction <= 1
    assert 0 <= location <= 1
    assert 0 <= hotel_depth <= 3000
    assert 1 <= grip_direction <= 4
    assert 0 <= collision_control_level <= 1

    await self.driver.send_command(
      module="C0",
      command="PC",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      hh=location,
      hd=hotel_depth,
      gr=grip_direction,
      ga=collision_control_level,
    )

  # -- R0 parameter helpers (private) ----------------------------------------

  async def _get_r0_parameter(self, name: str, fmt: str):
    """Read a single R0 parameter via RA command."""
    return (await self.driver.send_command("R0", "RA", ra=name, fmt=fmt))[name]

  async def _set_r0_parameter(self, **kwargs) -> None:
    """Set R0 parameter(s) via AA command."""
    await self.driver.send_command("R0", "AA", **kwargs)

  @asynccontextmanager
  async def slow(self, wrist_velocity: int = 20_000, gripper_velocity: int = 20_000):
    """Context manager that temporarily slows iSWAP wrist and gripper velocities (R0 RA/AA).

    Args:
      wrist_velocity: Wrist velocity in incr/sec (20..65000).
      gripper_velocity: Gripper velocity in incr/sec (20..75000).
    """
    assert 20 <= gripper_velocity <= 75_000
    assert 20 <= wrist_velocity <= 65_000

    original_wv = await self._get_r0_parameter("wv", "wv#####")
    original_tv = await self._get_r0_parameter("tv", "tv#####")

    await self._set_r0_parameter(wv=gripper_velocity)
    await self._set_r0_parameter(tv=wrist_velocity)
    try:
      yield
    finally:
      await self._set_r0_parameter(wv=original_wv)
      await self._set_r0_parameter(tv=original_tv)

  @dataclass
  class ParkParams(BackendParams):
    minimum_traverse_height: float = 280.0

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Park the iSWAP.

    Args:
      backend_params: iSWAP.ParkParams with minimum_traverse_height.
    """
    if not isinstance(backend_params, iSWAPBackend.ParkParams):
      backend_params = iSWAPBackend.ParkParams()

    if not 0 <= backend_params.minimum_traverse_height <= 360.0:
      raise ValueError("minimum_traverse_height must be between 0 and 360.0")

    await self.driver.send_command(
      module="C0",
      command="PG",
      th=round(backend_params.minimum_traverse_height * 10),
    )
    self._parked = True

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Open the iSWAP gripper.

    Args:
      gripper_width: Open position [mm].
      backend_params: Unused, reserved for future use.
    """
    if not 0 <= gripper_width <= 999.9:
      raise ValueError("gripper_width must be between 0 and 999.9")

    await self.driver.send_command(
      module="C0", command="GF", go=f"{round(gripper_width * 10):04}"
    )

  @dataclass
  class CloseGripperParams(BackendParams):
    grip_strength: int = 5
    plate_width_tolerance: float = 0

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Close the iSWAP gripper.

    Args:
      gripper_width: Plate width [mm].
      backend_params: iSWAP.CloseGripperParams with grip_strength and plate_width_tolerance.
    """
    if not isinstance(backend_params, iSWAPBackend.CloseGripperParams):
      backend_params = iSWAPBackend.CloseGripperParams()

    if not 0 <= backend_params.grip_strength <= 9:
      raise ValueError("grip_strength must be between 0 and 9")
    if not 0 <= gripper_width <= 999.9:
      raise ValueError("gripper_width must be between 0 and 999.9")
    if not 0 <= backend_params.plate_width_tolerance <= 9.9:
      raise ValueError("plate_width_tolerance must be between 0 and 9.9")

    await self.driver.send_command(
      module="C0",
      command="GC",
      gw=backend_params.grip_strength,
      gb=f"{round(gripper_width * 10):04}",
      gt=f"{round(backend_params.plate_width_tolerance * 10):02}",
    )

  @dataclass
  class PickUpParams(BackendParams):
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
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up a plate at the specified location.

    Args:
      location: Plate center position [mm].
      direction: Grip direction in degrees (0=front, 90=right, 180=back, 270=left).
      resource_width: Plate width [mm].
      backend_params: iSWAP.PickUpParams for firmware-specific settings.
    """
    if not isinstance(backend_params, iSWAPBackend.PickUpParams):
      backend_params = iSWAPBackend.PickUpParams()

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

    await self.driver.send_command(
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
  class DropParams(BackendParams):
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
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop a plate at the specified location.

    Args:
      location: Plate center position [mm].
      direction: Grip direction in degrees (0=front, 90=right, 180=back, 270=left).
      resource_width: Plate width [mm]. Used to compute open gripper position.
      backend_params: iSWAP.DropParams for firmware-specific settings.
    """
    if not isinstance(backend_params, iSWAPBackend.DropParams):
      backend_params = iSWAPBackend.DropParams()

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

    await self.driver.send_command(
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

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    """Check if the iSWAP is holding a plate.

    Returns:
      True if holding a plate, False otherwise.
    """
    resp = await self.driver.send_command(module="C0", command="QP", fmt="ph#")
    return resp is not None and resp["ph"] == 1

  @dataclass
  class MoveToLocationParams(BackendParams):
    minimum_traverse_height: float = 360.0
    collision_control_level: int = 1
    acceleration_index_high_acc: int = 4
    acceleration_index_low_acc: int = 1

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move a held plate to a new position without releasing it.

    Args:
      location: Target plate center position [mm].
      direction: Grip direction in degrees (0=front, 90=right, 180=back, 270=left).
      backend_params: iSWAP.MoveToLocationParams for firmware-specific settings.
    """
    if not isinstance(backend_params, iSWAPBackend.MoveToLocationParams):
      backend_params = iSWAPBackend.MoveToLocationParams()

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

    await self.driver.send_command(
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

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError("iSWAP halt not yet implemented")
