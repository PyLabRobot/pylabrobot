import asyncio
from typing import Any, List, Optional, Tuple

from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.ufactory.xarm6.joints import CartesianPose, JointPose


class XArm6Error(Exception):
  """Error raised when the xArm SDK returns a non-zero code."""

  def __init__(self, code: int, message: str):
    self.code = code
    super().__init__(f"XArm6Error {code}: {message}")


class XArm6:
  """Driver for the UFACTORY xArm 6 robotic arm.

  All lengths are millimeters and all angles are degrees, matching PLR
  conventions.

  Args:
    ip: IP address of the xArm controller.
    tcp_offset: Optional TCP offset (x, y, z, roll, pitch, yaw) for the end
      effector, in mm and degrees.
    tcp_load: Optional payload config as (mass_kg, [cx, cy, cz]).
    gripper_min_mm: Jaw width when fully closed, in mm.
    gripper_max_mm: Jaw width when fully open, in mm.
    closed_threshold_mm: Width at or below which the gripper counts as closed.
    park_location: Where :meth:`park` moves to. Defaults to the SDK home position.
    park_rotation: Rotation to hold at ``park_location``.
  """

  _MAX_GRIPPER_UNITS = 850

  def __init__(
    self,
    ip: str,
    tcp_offset: Optional[Tuple[float, float, float, float, float, float]] = None,
    tcp_load: Optional[Tuple[float, List[float]]] = None,
    gripper_min_mm: float = 71.0,
    gripper_max_mm: float = 150.0,
    closed_threshold_mm: float = 73.0,
    park_location: Optional[Coordinate] = None,
    park_rotation: Optional[Rotation] = None,
  ):
    super().__init__()
    self._ip = ip
    self._arm: Any = None
    self._tcp_offset = tcp_offset
    self._tcp_load = tcp_load
    self.gripper_min_mm = gripper_min_mm
    self.gripper_max_mm = gripper_max_mm
    self.closed_threshold_mm = closed_threshold_mm
    self.park_location = park_location
    self.park_rotation = park_rotation or Rotation()

  # -- SDK helpers -----------------------------------------------------------

  async def _call_sdk(self, func, *args, op: str = "", num_retries: int = 0, **kwargs):
    """Run a synchronous xArm SDK call in a thread, check the return code, and
    return any data payload.

    The xArm SDK uses two return conventions: command methods (``set_position``,
    ``set_gripper_position`` etc.) return a single ``int`` status code, while
    query methods (``get_position``, ``get_servo_angle`` etc.) return a
    ``(code, data)`` tuple.  This helper handles both: it raises
    :class:`XArm6Error` on a non-zero code and returns the data payload
    (unwrapped) for query methods, or ``None`` for command methods.

    If ``num_retries`` > 0, failed attempts trigger :meth:`clear_errors` and
    the call is retried up to that many additional times before propagating
    the last error.

    Args:
      func: Bound xArm SDK method to invoke.
      op: Short operation name used in error messages.
      num_retries: Number of retry attempts after :meth:`clear_errors` on
        failure.  Zero means "no retry".
      *args, **kwargs: Forwarded to ``func``.
    """
    code: Any = None
    for attempt in range(num_retries + 1):
      if attempt > 0:
        await self.clear_errors()
      result = await asyncio.to_thread(func, *args, **kwargs)
      if isinstance(result, (tuple, list)):
        code, *data_parts = result
      else:
        code, data_parts = result, []
      if code is None or code == 0:
        if not data_parts:
          return None
        return data_parts[0] if len(data_parts) == 1 else tuple(data_parts)
    raise XArm6Error(code, f"Failed during {op}" if op else "SDK call failed")

  # -- Lifecycle -------------------------------------------------------------

  async def clear_errors(self) -> None:
    """Clear errors/warnings and re-enable the robot for motion.

    This runs the full recovery sequence: clean errors, clean warnings,
    re-enable motion, set position control mode, and set ready state.
    Call this when the robot enters an error/protection state (e.g. code 9).
    """
    await self._call_sdk(self._arm.clean_error, op="clean_error")
    await self._call_sdk(self._arm.clean_warn, op="clean_warn")
    await self._call_sdk(self._arm.motion_enable, True, op="motion_enable")
    await self._call_sdk(self._arm.set_mode, 0, op="set_mode")
    await self._call_sdk(self._arm.set_state, 0, op="set_state")

  async def setup(self, skip_gripper_init: bool = False) -> None:
    """Connect to the xArm and initialize for position control.

    Args:
      skip_gripper_init: If True, skip gripper mode/enable during setup.
    """
    # xarm-python-sdk ships no stubs; when the xarm extra is absent the module is
    # missing outright, so both codes can fire depending on the environment.
    from xarm.wrapper import XArmAPI  # type: ignore[import-not-found,import-untyped]

    self._arm = XArmAPI(self._ip)
    await self.clear_errors()

    if self._tcp_offset is not None:
      await self._call_sdk(self._arm.set_tcp_offset, list(self._tcp_offset), op="set_tcp_offset")
    if self._tcp_load is not None:
      await self._call_sdk(
        self._arm.set_tcp_load, self._tcp_load[0], self._tcp_load[1], op="set_tcp_load"
      )

    if not skip_gripper_init:
      await self._call_sdk(self._arm.set_gripper_mode, 1, op="set_gripper_mode")
      await self._call_sdk(self._arm.set_gripper_enable, True, op="set_gripper_enable")

    # Re-assert position control mode after gripper init (gripper mode change
    # can reset the arm state on some firmware versions)
    await self._call_sdk(self._arm.set_mode, 0, op="set_mode")
    await self._call_sdk(self._arm.set_state, 0, op="set_state")

  async def stop(self) -> None:
    """Disconnect from the xArm."""
    if self._arm is not None:
      await self._call_sdk(self._arm.disconnect, op="disconnect")
      self._arm = None

  # -- Conversion helpers ----------------------------------------------------

  def _mm_to_gripper_units(self, width_mm: float) -> int:
    clamped = max(self.gripper_min_mm, min(self.gripper_max_mm, width_mm))
    fraction = (clamped - self.gripper_min_mm) / (self.gripper_max_mm - self.gripper_min_mm)
    return int(round(fraction * self._MAX_GRIPPER_UNITS))

  def _gripper_units_to_mm(self, units: int) -> float:
    fraction = units / self._MAX_GRIPPER_UNITS
    return self.gripper_min_mm + fraction * (self.gripper_max_mm - self.gripper_min_mm)

  async def _set_gripper_units(self, units: int) -> None:
    await self._call_sdk(
      self._arm.set_gripper_position,
      units,
      wait=True,
      speed=0,
      op="set_gripper_position",
    )

  # -- Gripper ---------------------------------------------------------------

  @property
  def min_gripper_width(self) -> float:
    return self.gripper_min_mm

  @property
  def max_gripper_width(self) -> float:
    return self.gripper_max_mm

  async def move_gripper(self, width: float, force_sensing: bool = False) -> None:
    """Move the bio-gripper jaws to ``width`` mm.

    The xArm bio-gripper is position-controlled; ``force_sensing`` has no
    effect on the SDK call.
    """
    await self._set_gripper_units(self._mm_to_gripper_units(width))

  async def is_gripper_closed(self) -> bool:
    """Return True if the gripper width is at or below ``closed_threshold_mm``."""
    units = await self._call_sdk(self._arm.get_gripper_position, op="get_gripper_position")
    return self._gripper_units_to_mm(int(units)) <= self.closed_threshold_mm

  # -- Arm -------------------------------------------------------------------

  async def halt(self) -> None:
    """Emergency stop all motion."""
    await self._call_sdk(self._arm.emergency_stop, op="emergency_stop")

  async def park(self) -> None:
    """Move to ``park_location`` if set, otherwise to the SDK home position.

    If the SDK fails on the first ``move_gohome`` call, errors are cleared and
    the call is retried once automatically.
    """
    if self.park_location is not None:
      await self.move_to_location(self.park_location, self.park_rotation)
      return
    await self._call_sdk(
      self._arm.move_gohome,
      speed=50,
      mvacc=5000,
      wait=True,
      op="move_gohome",
      num_retries=1,
    )

  async def request_gripper_pose(self) -> CartesianPose:
    """Get the current gripper location and rotation."""
    pose = await self._call_sdk(self._arm.get_position, op="get_position")
    return CartesianPose(
      location=Coordinate(x=pose[0], y=pose[1], z=pose[2]),
      rotation=Rotation(x=pose[3], y=pose[4], z=pose[5]),
    )

  # -- Cartesian motion ------------------------------------------------------

  async def move_to_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    speed: float = 100.0,
    mvacc: float = 2000.0,
  ) -> None:
    """Move to a Cartesian location with the given rotation.

    Args:
      location: Target location.
      rotation: Target rotation.
      speed: Cartesian move speed in mm/s.
      mvacc: Cartesian move acceleration in mm/s^2.
    """
    await self._call_sdk(
      self._arm.set_position,
      x=location.x,
      y=location.y,
      z=location.z,
      roll=rotation.x,
      pitch=rotation.y,
      yaw=rotation.z,
      speed=speed,
      mvacc=mvacc,
      wait=True,
      op="set_position",
    )

  async def pick_up_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    speed: float = 100.0,
    mvacc: float = 2000.0,
  ) -> None:
    """Move to ``location`` and close the gripper to ``resource_width``.

    Args:
      location: Target location.
      rotation: Target rotation.
      resource_width: Width to close the gripper to, in mm.
      speed: Cartesian move speed in mm/s.
      mvacc: Cartesian move acceleration in mm/s^2.
    """
    await self.move_to_location(location, rotation, speed=speed, mvacc=mvacc)
    await self.move_gripper(width=resource_width, force_sensing=True)

  async def drop_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    speed: float = 100.0,
    mvacc: float = 2000.0,
  ) -> None:
    """Move to ``location`` and fully open the gripper.

    Args:
      location: Target location.
      rotation: Target rotation.
      resource_width: Width of the resource being released, in mm. The gripper
        opens fully regardless.
      speed: Cartesian move speed in mm/s.
      mvacc: Cartesian move acceleration in mm/s^2.
    """
    await self.move_to_location(location, rotation, speed=speed, mvacc=mvacc)
    await self._set_gripper_units(self._MAX_GRIPPER_UNITS)

  # -- Joint motion ----------------------------------------------------------

  async def move_to_joint_position(
    self,
    position: JointPose,
    speed: float = 50.0,
    mvacc: float = 500.0,
  ) -> None:
    """Move to the specified joint angles.

    Missing joint indices are filled in from the current joint position.

    Args:
      position: Target joint angles in degrees, keyed by axis number.
      speed: Joint move speed in deg/s.
      mvacc: Joint move acceleration in deg/s^2.
    """
    current_angles = await self._call_sdk(self._arm.get_servo_angle, op="get_servo_angle")
    angles = list(current_angles)
    for axis, value in position.items():
      angles[int(axis) - 1] = value
    await self._call_sdk(
      self._arm.set_servo_angle,
      angle=angles,
      speed=speed,
      mvacc=mvacc,
      wait=True,
      op="set_servo_angle",
    )

  async def request_joint_position(self) -> JointPose:
    """Get current joint angles as ``{1: j1_deg, 2: j2_deg, ...}``."""
    angles = await self._call_sdk(self._arm.get_servo_angle, op="get_servo_angle")
    return {i + 1: angles[i] for i in range(6)}

  async def pick_up_at_joint_position(
    self,
    position: JointPose,
    resource_width: float,
    speed: float = 50.0,
    mvacc: float = 500.0,
  ) -> None:
    """Move to the joint target and close the gripper to ``resource_width``.

    Args:
      position: Target joint angles in degrees, keyed by axis number.
      resource_width: Width to close the gripper to, in mm.
      speed: Joint move speed in deg/s.
      mvacc: Joint move acceleration in deg/s^2.
    """
    await self.move_to_joint_position(position, speed=speed, mvacc=mvacc)
    await self.move_gripper(width=resource_width, force_sensing=True)

  async def drop_at_joint_position(
    self,
    position: JointPose,
    resource_width: float,
    speed: float = 50.0,
    mvacc: float = 500.0,
  ) -> None:
    """Move to the joint target and fully open the gripper.

    Args:
      position: Target joint angles in degrees, keyed by axis number.
      resource_width: Width of the resource being released, in mm. The gripper
        opens fully regardless.
      speed: Joint move speed in deg/s.
      mvacc: Joint move acceleration in deg/s^2.
    """
    await self.move_to_joint_position(position, speed=speed, mvacc=mvacc)
    await self._set_gripper_units(self._MAX_GRIPPER_UNITS)

  # -- Freedrive -------------------------------------------------------------

  async def start_freedrive_mode(self, free_axes: List[int]) -> None:
    """Enter freedrive (manual teaching) mode.

    The xArm SDK only supports freeing all axes at once, so ``free_axes`` has
    no effect.
    """
    await self._call_sdk(self._arm.set_mode, 2, op="set_mode")
    await self._call_sdk(self._arm.set_state, 0, op="set_state")

  async def stop_freedrive_mode(self) -> None:
    """Exit freedrive mode and return to position control."""
    await self._call_sdk(self._arm.set_mode, 0, op="set_mode")
    await self._call_sdk(self._arm.set_state, 0, op="set_state")
