import functools
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable, List, Optional, Tuple

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.ufactory.xarm6.joints import CartesianPose, JointPose


class XArm6Error(Exception):
  """Error raised when the xArm SDK returns a non-zero code."""

  def __init__(self, code: int, message: str):
    self.code = code
    super().__init__(f"XArm6Error {code}: {message}")


class XArm6(HasLifespan):
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
    self._sdk_limiter: Optional[anyio.CapacityLimiter] = None
    self._freedrive_active = False
    self._freedrive_state_unknown = False
    self._freedrive_scope_token: Optional[object] = None
    self._freedrive_transition_lock = anyio.Lock()
    self._arm_motion_owner: ContextVar[Optional[int]] = ContextVar(
      "xarm6_arm_motion_owner", default=None
    )
    self._tcp_offset = tcp_offset
    self._tcp_load = tcp_load
    self.gripper_min_mm = gripper_min_mm
    self.gripper_max_mm = gripper_max_mm
    self.closed_threshold_mm = closed_threshold_mm
    self.park_location = park_location
    self.park_rotation = park_rotation or Rotation()

  # -- SDK helpers -----------------------------------------------------------

  def _get_active_arm(self) -> Any:
    """Return the current SDK handle only while the public lifespan is active."""
    if not self.is_active or self._arm is None:
      raise RuntimeError("XArm6 has no active lifespan")
    return self._arm

  async def _call_sdk(
    self,
    func,
    *args,
    op: str = "",
    num_retries: int = 0,
    _on_dispatch: Optional[Callable[[], None]] = None,
    **kwargs,
  ):
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
      _on_dispatch: Private hook called in the worker immediately before the SDK function.
      *args, **kwargs: Forwarded to ``func``.
    """
    code: Any = None
    limiter = self._sdk_limiter
    if limiter is None:
      raise RuntimeError("XArm6 has no active lifespan")

    def invoke():
      if _on_dispatch is not None:
        _on_dispatch()
      return func(*args, **kwargs)

    for attempt in range(num_retries + 1):
      if attempt > 0:
        await self._clear_errors(self._get_active_arm())
      result = await anyio.to_thread.run_sync(
        invoke,
        abandon_on_cancel=False,
        limiter=limiter,
      )
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
    async with self._freedrive_transition_lock:
      self._reject_freedrive_scope()
      await self._clear_errors(self._get_active_arm())

  async def _clear_errors(self, arm: Any) -> None:
    """Run the recovery sequence on a lifespan-owned SDK handle."""
    await self._call_sdk(arm.clean_error, op="clean_error")
    await self._call_sdk(arm.clean_warn, op="clean_warn")
    await self._call_sdk(arm.motion_enable, True, op="motion_enable")
    await self._call_sdk(arm.set_mode, 0, op="set_mode")
    await self._call_sdk(arm.set_state, 0, op="set_state")
    self._freedrive_active = False
    self._freedrive_state_unknown = False

  async def _disconnect_arm(self, arm: Any) -> None:
    """Disconnect one lifespan's SDK handle and clear it from the driver."""
    try:
      await self._call_sdk(arm.disconnect, op="disconnect")
    finally:
      if self._arm is arm:
        self._arm = None

  def _clear_sdk_limiter(self, limiter: anyio.CapacityLimiter) -> None:
    """Clear one lifespan's SDK limiter after disconnect completes."""
    if self._sdk_limiter is limiter:
      self._sdk_limiter = None

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    skip_gripper_init: bool = False,
  ) -> None:
    """Connect to the xArm and initialize for position control.

    Args:
      stack: Exit stack that owns the SDK connection.
      skip_gripper_init: Skip gripper initialization.
    """
    await super()._enter_lifespan(stack)
    if not isinstance(skip_gripper_init, bool):
      raise TypeError("skip_gripper_init must be a bool")

    limiter = anyio.CapacityLimiter(1)
    self._sdk_limiter = limiter
    stack.callback(self._clear_sdk_limiter, limiter)

    # xarm-python-sdk ships no stubs; when the xarm extra is absent the module is
    # missing outright, so both codes can fire depending on the environment.
    from xarm.wrapper import XArmAPI  # type: ignore[import-not-found,import-untyped]

    arm = await anyio.to_thread.run_sync(
      functools.partial(XArmAPI, self._ip),
      abandon_on_cancel=False,
      limiter=limiter,
    )
    self._arm = arm
    stack.push_shielded_async_callback(self._disconnect_arm, arm)
    self._freedrive_active = False
    self._freedrive_state_unknown = False
    self._freedrive_scope_token = None
    stack.push_shielded_async_callback(self._force_restore_position_mode, arm)
    await self._clear_errors(arm)

    if self._tcp_offset is not None:
      await self._call_sdk(arm.set_tcp_offset, list(self._tcp_offset), op="set_tcp_offset")
    if self._tcp_load is not None:
      await self._call_sdk(
        arm.set_tcp_load, self._tcp_load[0], self._tcp_load[1], op="set_tcp_load"
      )

    if not skip_gripper_init:
      await self._call_sdk(arm.set_gripper_mode, 1, op="set_gripper_mode")
      await self._call_sdk(arm.set_gripper_enable, True, op="set_gripper_enable")

    # Re-assert position control mode after gripper init (gripper mode change
    # can reset the arm state on some firmware versions)
    await self._call_sdk(arm.set_mode, 0, op="set_mode")
    await self._call_sdk(arm.set_state, 0, op="set_state")
    await anyio.lowlevel.checkpoint()

  # -- Conversion helpers ----------------------------------------------------

  def _mm_to_gripper_units(self, width_mm: float) -> int:
    clamped = max(self.gripper_min_mm, min(self.gripper_max_mm, width_mm))
    fraction = (clamped - self.gripper_min_mm) / (self.gripper_max_mm - self.gripper_min_mm)
    return int(round(fraction * self._MAX_GRIPPER_UNITS))

  def _gripper_units_to_mm(self, units: int) -> float:
    fraction = units / self._MAX_GRIPPER_UNITS
    return self.gripper_min_mm + fraction * (self.gripper_max_mm - self.gripper_min_mm)

  async def _set_gripper_units(self, units: int) -> None:
    arm = self._get_active_arm()
    await self._call_sdk(
      arm.set_gripper_position,
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
    arm = self._get_active_arm()
    units = await self._call_sdk(arm.get_gripper_position, op="get_gripper_position")
    return self._gripper_units_to_mm(int(units)) <= self.closed_threshold_mm

  # -- Arm -------------------------------------------------------------------

  async def halt(self) -> None:
    """Emergency stop all motion."""
    arm = self._get_active_arm()
    await self._call_sdk(arm.emergency_stop, op="emergency_stop")

  async def park(self) -> None:
    """Move to ``park_location`` if set, otherwise to the SDK home position.

    If the SDK fails on the first ``move_gohome`` call, errors are cleared and
    the call is retried once automatically.
    """
    async with self._arm_motion_admission():
      if self.park_location is not None:
        await self.move_to_location(self.park_location, self.park_rotation)
        return
      arm = self._get_active_arm()
      await self._call_sdk(
        arm.move_gohome,
        speed=50,
        mvacc=5000,
        wait=True,
        op="move_gohome",
        num_retries=1,
      )

  async def request_gripper_pose(self) -> CartesianPose:
    """Get the current gripper location and rotation."""
    arm = self._get_active_arm()
    pose = await self._call_sdk(arm.get_position, op="get_position")
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
    async with self._arm_motion_admission():
      arm = self._get_active_arm()
      await self._call_sdk(
        arm.set_position,
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
    async with self._arm_motion_admission():
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
    async with self._arm_motion_admission():
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
    async with self._arm_motion_admission():
      arm = self._get_active_arm()
      current_angles = await self._call_sdk(arm.get_servo_angle, op="get_servo_angle")
      angles = list(current_angles)
      for axis, value in position.items():
        angles[int(axis) - 1] = value
      await self._call_sdk(
        arm.set_servo_angle,
        angle=angles,
        speed=speed,
        mvacc=mvacc,
        wait=True,
        op="set_servo_angle",
      )

  async def request_joint_position(self) -> JointPose:
    """Get current joint angles as ``{1: j1_deg, 2: j2_deg, ...}``."""
    arm = self._get_active_arm()
    angles = await self._call_sdk(arm.get_servo_angle, op="get_servo_angle")
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
    async with self._arm_motion_admission():
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
    async with self._arm_motion_admission():
      await self.move_to_joint_position(position, speed=speed, mvacc=mvacc)
      await self._set_gripper_units(self._MAX_GRIPPER_UNITS)

  # -- Freedrive -------------------------------------------------------------

  def _reject_freedrive_scope(self) -> None:
    """Reject direct transitions while a freedrive scope owns the arm."""
    if self._freedrive_scope_token is not None:
      raise RuntimeError("XArm6 freedrive is scoped")

  def _reject_freedrive_conflict(self) -> None:
    """Reject arm mutations while freedrive may still control the arm."""
    self._reject_freedrive_scope()
    if self._freedrive_active or self._freedrive_state_unknown:
      raise RuntimeError("XArm6 freedrive state requires recovery")

  @asynccontextmanager
  async def _arm_motion_admission(self) -> AsyncIterator[None]:
    """Atomically exclude scoped freedrive from one complete arm mutation."""
    task_id = anyio.get_current_task().id
    if self._arm_motion_owner.get() == task_id:
      yield
      return

    self._reject_freedrive_conflict()
    async with self._freedrive_transition_lock:
      self._reject_freedrive_conflict()
      owner_token = self._arm_motion_owner.set(task_id)
      try:
        yield
      finally:
        self._arm_motion_owner.reset(owner_token)

  async def _restore_position_mode(self, arm: Any) -> None:
    """Restore position mode and retain retry state if either SDK call fails."""
    try:
      await self._call_sdk(arm.set_mode, 0, op="set_mode")
      await anyio.lowlevel.checkpoint()
      await self._call_sdk(arm.set_state, 0, op="set_state")
      await anyio.lowlevel.checkpoint()
    except BaseException:
      self._freedrive_active = True
      self._freedrive_state_unknown = True
      raise
    else:
      self._freedrive_active = False
      self._freedrive_state_unknown = False

  async def _start_freedrive_transition(self, arm: Any) -> None:
    """Enter mode 2 and restore mode 0 if the transition does not complete."""
    await anyio.lowlevel.checkpoint()
    mode_change_dispatched = False

    def mark_mode_change_dispatched() -> None:
      nonlocal mode_change_dispatched
      mode_change_dispatched = True
      self._freedrive_state_unknown = True

    try:
      await self._call_sdk(
        arm.set_mode,
        2,
        op="set_mode",
        _on_dispatch=mark_mode_change_dispatched,
      )
      await anyio.lowlevel.checkpoint()
      await self._call_sdk(arm.set_state, 0, op="set_state")
      await anyio.lowlevel.checkpoint()
    except BaseException:
      if mode_change_dispatched:
        with anyio.CancelScope(shield=True):
          await self._restore_position_mode(arm)
      else:
        self._freedrive_state_unknown = False
      raise
    else:
      self._freedrive_active = True
      self._freedrive_state_unknown = False

  async def _force_restore_position_mode(self, arm: Any) -> None:
    """Restore tracked freedrive state before disconnect and invalidate ownership."""
    async with self._freedrive_transition_lock:
      try:
        if (
          self._freedrive_active
          or self._freedrive_state_unknown
          or self._freedrive_scope_token is not None
        ):
          await self._restore_position_mode(arm)
      finally:
        self._freedrive_active = False
        self._freedrive_state_unknown = False
        self._freedrive_scope_token = None

  async def start_freedrive_mode(self, free_axes: List[int]) -> None:
    """Enter freedrive (manual teaching) mode.

    The xArm SDK only supports freeing all axes at once, so ``free_axes`` has
    no effect.
    """
    del free_axes
    async with self._freedrive_transition_lock:
      self._reject_freedrive_conflict()
      await self._start_freedrive_transition(self._get_active_arm())

  async def stop_freedrive_mode(self) -> None:
    """Exit freedrive mode and return to position control."""
    async with self._freedrive_transition_lock:
      self._reject_freedrive_scope()
      await self._restore_position_mode(self._get_active_arm())

  @asynccontextmanager
  async def freedrive(self, free_axes: List[int]) -> AsyncIterator[None]:
    """Temporarily enable all-axis manual teaching and restore position mode."""
    del free_axes
    token = object()
    async with self._freedrive_transition_lock:
      self._get_active_arm()
      if self._freedrive_scope_token is not None:
        raise RuntimeError("XArm6 freedrive is already scoped")
      if self._freedrive_active or self._freedrive_state_unknown:
        raise RuntimeError("XArm6 freedrive is already active")
      self._freedrive_scope_token = token
      try:
        await self._start_freedrive_transition(self._get_active_arm())
      except BaseException:
        self._freedrive_scope_token = None
        raise

    try:
      await anyio.lowlevel.checkpoint()
      yield
    finally:
      with anyio.CancelScope(shield=True):
        async with self._freedrive_transition_lock:
          if self._freedrive_scope_token is token:
            try:
              await self._restore_position_mode(self._get_active_arm())
            finally:
              self._freedrive_scope_token = None
