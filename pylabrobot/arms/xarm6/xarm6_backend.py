import asyncio
from typing import Any, Dict, Optional, Tuple, Union

from pylabrobot.arms.backend import AccessPattern, HorizontalAccess, VerticalAccess
from pylabrobot.arms.six_axis_backend import SixAxisBackend
from pylabrobot.arms.standard import CartesianCoords
from pylabrobot.resources import Coordinate, Rotation


class XArm6Error(Exception):
  """Error raised when the xArm SDK returns a non-zero code."""

  def __init__(self, code: int, message: str):
    self.code = code
    super().__init__(f"XArm6Error {code}: {message}")


class XArm6Backend(SixAxisBackend):
  """Backend for the UFACTORY xArm 6 robotic arm with bio-gripper.

  Uses the xArm Python SDK (xarm-python-sdk) to communicate with the robot
  over a network connection.

  Args:
    ip: IP address of the xArm controller.
    default_speed: Default Cartesian move speed in mm/s.
    default_mvacc: Default Cartesian move acceleration in mm/s^2.
    default_joint_speed: Default joint move speed in deg/s.
    default_joint_mvacc: Default joint move acceleration in deg/s^2.
    safe_position: Optional predefined safe position for move_to_safe().
    tcp_offset: Optional TCP offset (x, y, z, roll, pitch, yaw) for the end effector.
    tcp_load: Optional payload config as (mass_kg, [cx, cy, cz]).
    gripper_open_pos: Default gripper open position for pick/place sequences.
    gripper_close_pos: Default gripper close position for pick/place sequences.
  """

  def __init__(
    self,
    ip: str,
    default_speed: float = 100.0,
    default_mvacc: float = 2000.0,
    default_joint_speed: float = 50.0,
    default_joint_mvacc: float = 500.0,
    safe_position: Optional[CartesianCoords] = None,
    tcp_offset: Optional[Tuple[float, float, float, float, float, float]] = None,
    tcp_load: Optional[Tuple[float, list]] = None,
    gripper_open_pos: int = 850,
    gripper_close_pos: int = 0,
  ):
    super().__init__()
    self._ip = ip
    self._arm: Any = None
    self._default_speed = default_speed
    self._default_mvacc = default_mvacc
    self._default_joint_speed = default_joint_speed
    self._default_joint_mvacc = default_joint_mvacc
    self._safe_position = safe_position
    self._tcp_offset = tcp_offset
    self._tcp_load = tcp_load
    self._gripper_open_pos = gripper_open_pos
    self._gripper_close_pos = gripper_close_pos

  # -- Speed/acceleration get/set --

  @property
  def speed(self) -> float:
    """Current default Cartesian move speed in mm/s."""
    return self._default_speed

  @speed.setter
  def speed(self, value: float) -> None:
    self._default_speed = value

  @property
  def mvacc(self) -> float:
    """Current default Cartesian move acceleration in mm/s^2."""
    return self._default_mvacc

  @mvacc.setter
  def mvacc(self, value: float) -> None:
    self._default_mvacc = value

  @property
  def joint_speed(self) -> float:
    """Current default joint move speed in deg/s."""
    return self._default_joint_speed

  @joint_speed.setter
  def joint_speed(self, value: float) -> None:
    self._default_joint_speed = value

  @property
  def joint_mvacc(self) -> float:
    """Current default joint move acceleration in deg/s^2."""
    return self._default_joint_mvacc

  @joint_mvacc.setter
  def joint_mvacc(self, value: float) -> None:
    self._default_joint_mvacc = value

  async def _call_sdk(self, func, *args, **kwargs):
    """Run a synchronous xArm SDK call in a thread executor to avoid blocking."""
    return await asyncio.to_thread(func, *args, **kwargs)

  def _check_result(self, code, operation: str = ""):
    """Raise XArm6Error if the SDK return code indicates failure."""
    if code != 0:
      raise XArm6Error(code, f"Failed during {operation}" if operation else "SDK call failed")

  async def clear_errors(self) -> None:
    """Clear errors/warnings and re-enable the robot for motion.

    This runs the full recovery sequence: clean errors, clean warnings,
    re-enable motion, set position control mode, and set ready state.
    Call this when the robot enters an error/protection state (e.g. code 9).
    """
    await self._call_sdk(self._arm.clean_error)
    await self._call_sdk(self._arm.clean_warn)
    await self._call_sdk(self._arm.motion_enable, True)
    await self._call_sdk(self._arm.set_mode, 0)
    await self._call_sdk(self._arm.set_state, 0)

  async def setup(self):
    """Connect to the xArm and initialize for position control."""
    from xarm.wrapper import XArmAPI  # type: ignore[import-not-found]

    self._arm = XArmAPI(self._ip)
    await self.clear_errors()

    if self._tcp_offset is not None:
      await self._call_sdk(self._arm.set_tcp_offset, list(self._tcp_offset))
    if self._tcp_load is not None:
      await self._call_sdk(self._arm.set_tcp_load, self._tcp_load[0], self._tcp_load[1])

    await self._call_sdk(self._arm.set_gripper_mode, 0)
    await self._call_sdk(self._arm.set_gripper_enable, True)

  async def stop(self):
    """Disconnect from the xArm."""
    if self._arm is not None:
      await self._call_sdk(self._arm.disconnect)
      self._arm = None

  async def move_to(self, position: Union[CartesianCoords, Dict[int, float]]) -> None:
    """Move the arm to a Cartesian position or joint angles.

    Args:
      position: Either a CartesianCoords for Cartesian moves, or a Dict mapping
                joint index (1-6) to angle in degrees for joint moves.
    """
    if isinstance(position, CartesianCoords):
      code = await self._call_sdk(
        self._arm.set_position,
        x=position.location.x,
        y=position.location.y,
        z=position.location.z,
        roll=position.rotation.x,
        pitch=position.rotation.y,
        yaw=position.rotation.z,
        speed=self._default_speed,
        mvacc=self._default_mvacc,
        wait=True,
      )
      self._check_result(code, "set_position")
    elif isinstance(position, dict):
      current_code, current_angles = await self._call_sdk(self._arm.get_servo_angle)
      self._check_result(current_code, "get_servo_angle")
      angles = list(current_angles)
      for axis, value in position.items():
        angles[int(axis) - 1] = value
      code = await self._call_sdk(
        self._arm.set_servo_angle,
        angle=angles,
        speed=self._default_joint_speed,
        mvacc=self._default_joint_mvacc,
        wait=True,
      )
      self._check_result(code, "set_servo_angle")
    else:
      raise TypeError(f"Position must be CartesianCoords or Dict[int, float], got {type(position)}")

  async def home(self) -> None:
    """Move the arm to its home (zero) position.

    If the robot is in an error state, this will attempt to clear errors
    and retry once before raising.
    """
    code = await self._call_sdk(self._arm.move_gohome, speed=50, mvacc=5000, wait=True)
    if code != 0:
      await self.clear_errors()
      code = await self._call_sdk(self._arm.move_gohome, speed=50, mvacc=5000, wait=True)
      self._check_result(code, "move_gohome")

  async def halt(self) -> None:
    """Emergency stop all motion."""
    await self._call_sdk(self._arm.emergency_stop)

  async def move_to_safe(self) -> None:
    """Move to the predefined safe position, or home if none is set."""
    if self._safe_position is not None:
      await self.move_to(self._safe_position)
    else:
      await self.home()

  async def get_joint_position(self) -> Dict[int, float]:
    """Get current joint angles as {1: j1_deg, 2: j2_deg, ...}."""
    code, angles = await self._call_sdk(self._arm.get_servo_angle)
    self._check_result(code, "get_servo_angle")
    return {i + 1: angles[i] for i in range(6)}

  async def get_cartesian_position(self) -> CartesianCoords:
    """Get the current Cartesian position and orientation."""
    code, pose = await self._call_sdk(self._arm.get_position)
    self._check_result(code, "get_position")
    return CartesianCoords(
      location=Coordinate(x=pose[0], y=pose[1], z=pose[2]),
      rotation=Rotation(x=pose[3], y=pose[4], z=pose[5]),
    )

  async def open_gripper(self, position: int, speed: int = 0) -> None:
    """Open the gripper to a target position.

    Args:
      position: Target open position (gripper-specific units, e.g. 0-850 for xArm gripper).
      speed: Gripper speed (0 = default/max).
    """
    code = await self._call_sdk(self._arm.set_gripper_position, position, wait=True, speed=speed)
    self._check_result(code, "set_gripper_position (open)")

  async def close_gripper(self, position: int, speed: int = 0) -> None:
    """Close the gripper to a target position.

    Args:
      position: Target close position (gripper-specific units, e.g. 0-850 for xArm gripper).
      speed: Gripper speed (0 = default/max).
    """
    code = await self._call_sdk(self._arm.set_gripper_position, position, wait=True, speed=speed)
    self._check_result(code, "set_gripper_position (close)")

  async def freedrive_mode(self) -> None:
    """Enter freedrive (manual teaching) mode."""
    await self._call_sdk(self._arm.set_mode, 2)
    await self._call_sdk(self._arm.set_state, 0)

  async def end_freedrive_mode(self) -> None:
    """Exit freedrive mode and return to position control."""
    await self._call_sdk(self._arm.set_mode, 0)
    await self._call_sdk(self._arm.set_state, 0)

  # -- Access pattern helpers --

  def _offset_position(
    self,
    position: CartesianCoords,
    dx: float = 0,
    dy: float = 0,
    dz: float = 0,
  ) -> CartesianCoords:
    """Create a new CartesianCoords offset from the given position."""
    return CartesianCoords(
      location=Coordinate(
        x=position.location.x + dx,
        y=position.location.y + dy,
        z=position.location.z + dz,
      ),
      rotation=position.rotation,
    )

  def _require_cartesian(self, position, method_name: str) -> CartesianCoords:
    if not isinstance(position, CartesianCoords):
      raise TypeError(f"{method_name} only supports CartesianCoords for xArm6.")
    return position

  # -- Approach --

  async def approach(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ) -> None:
    """Move to an approach position offset from the target."""
    pos = self._require_cartesian(position, "approach")
    if access is None:
      access = VerticalAccess()

    if isinstance(access, VerticalAccess):
      await self.move_to(self._offset_position(pos, dz=access.approach_height_mm))
    elif isinstance(access, HorizontalAccess):
      await self.move_to(self._offset_position(pos, dy=-access.approach_distance_mm))
    else:
      raise TypeError(f"Unsupported access pattern: {type(access)}")

  # -- Pick (vertical) --

  async def _pick_vertical(self, position: CartesianCoords, access: VerticalAccess) -> None:
    await self.open_gripper(self._gripper_open_pos)
    await self.move_to(self._offset_position(position, dz=access.approach_height_mm))
    await self.move_to(position)
    await self.close_gripper(self._gripper_close_pos)
    await self.move_to(self._offset_position(position, dz=access.clearance_mm))

  # -- Place (vertical) --

  async def _place_vertical(self, position: CartesianCoords, access: VerticalAccess) -> None:
    place_z_offset = access.gripper_offset_mm
    await self.move_to(
      self._offset_position(position, dz=place_z_offset + access.approach_height_mm)
    )
    await self.move_to(self._offset_position(position, dz=place_z_offset))
    await self.open_gripper(self._gripper_open_pos)
    await self.move_to(self._offset_position(position, dz=place_z_offset + access.clearance_mm))

  # -- Pick (horizontal) --

  async def _pick_horizontal(self, position: CartesianCoords, access: HorizontalAccess) -> None:
    await self.open_gripper(self._gripper_open_pos)
    await self.move_to(self._offset_position(position, dy=-access.approach_distance_mm))
    await self.move_to(position)
    await self.close_gripper(self._gripper_close_pos)
    retract = self._offset_position(position, dy=-access.clearance_mm)
    await self.move_to(retract)
    await self.move_to(self._offset_position(retract, dz=access.lift_height_mm))

  # -- Place (horizontal) --

  async def _place_horizontal(self, position: CartesianCoords, access: HorizontalAccess) -> None:
    place_z_offset = access.gripper_offset_mm
    above = self._offset_position(
      position, dy=-access.clearance_mm, dz=access.lift_height_mm + place_z_offset
    )
    await self.move_to(above)
    approach = self._offset_position(position, dy=-access.clearance_mm, dz=place_z_offset)
    await self.move_to(approach)
    await self.move_to(self._offset_position(position, dz=place_z_offset))
    await self.open_gripper(self._gripper_open_pos)
    await self.move_to(self._offset_position(position, dy=-access.clearance_mm, dz=place_z_offset))
    await self.move_to(
      self._offset_position(
        position, dy=-access.clearance_mm, dz=access.lift_height_mm + place_z_offset
      )
    )

  # -- Public pick/place --

  async def pick_up_resource(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ) -> None:
    """Pick a resource using the appropriate access pattern."""
    pos = self._require_cartesian(position, "pick_up_resource")
    if access is None:
      access = VerticalAccess()

    if isinstance(access, VerticalAccess):
      await self._pick_vertical(pos, access)
    elif isinstance(access, HorizontalAccess):
      await self._pick_horizontal(pos, access)
    else:
      raise TypeError(f"Unsupported access pattern: {type(access)}")

  async def drop_resource(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ) -> None:
    """Place a resource using the appropriate access pattern."""
    pos = self._require_cartesian(position, "drop_resource")
    if access is None:
      access = VerticalAccess()

    if isinstance(access, VerticalAccess):
      await self._place_vertical(pos, access)
    elif isinstance(access, HorizontalAccess):
      await self._place_horizontal(pos, access)
    else:
      raise TypeError(f"Unsupported access pattern: {type(access)}")
