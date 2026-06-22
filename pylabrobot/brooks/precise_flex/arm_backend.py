"""PreciseFlex arm capability backend - protocol translation and capability methods."""

import dataclasses
import logging
import warnings
from abc import ABC
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from pylabrobot.capabilities.arms.backend import (
  CanFreedrive,
  HasJoints,
  OrientableGripperArmBackend,
)
from pylabrobot.capabilities.arms.standard import JointPose
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Rotation

from . import kinematics
from .config import Axis, PreciseFlexConfiguration
from .confirmed_firmware_versions import (
  SUPPORTED_ROBOT_TYPES,
  is_confirmed,
  is_supported_model,
  suggest_entry,
)
from .data_ids import DataID
from .driver import PreciseFlexDriver
from .errors import PreciseFlexError
from .kinematics import ElbowOrientation, PreciseFlexCartesianPose, Wrist
from .tcs_modules import missing_required_modules

logger = logging.getLogger(__name__)


def _parse_scalar(response: str) -> float:
  """Parse the first numeric field of a DataID reply.

  Some scalar DataIDs come back zero-padded (e.g. robot type as ``12, 0, 0, ...``)
  and Cartesian references carry several components; take the leading value.
  """
  return float(response.split(",")[0])


def _parse_per_axis(response: str) -> Dict[Axis, float]:
  """Parse a comma-separated per-axis DataID reply into an {Axis: value} map."""
  values = [float(v) for v in response.split(",")]
  return {Axis(i + 1): values[i] for i in range(min(len(values), len(Axis)))}


def _zip_axis_ranges(
  low: Dict[Axis, float], high: Dict[Axis, float]
) -> Dict[Axis, tuple[float, float]]:
  """Combine min and max per-axis maps into an {Axis: (min, max)} map."""
  return {axis: (low[axis], high[axis]) for axis in low.keys() & high.keys()}


def _snap_to_current(ik_joints: JointPose, current: JointPose, wrist: Wrist) -> JointPose:
  """Shift each rotary joint by 360° multiples toward `current`, then re-enforce
  the wrist-sign half on J4 so the result still matches `wrist`. Avoids
  gratuitous full-turn moves when multiple IK solutions are equivalent.
  """
  out = dict(ik_joints)
  for axis in (Axis.SHOULDER, Axis.ELBOW, Axis.WRIST):
    out[axis] += 360 * round((current[axis] - out[axis]) / 360)
  if wrist == "ccw" and out[Axis.WRIST] < 0:
    out[Axis.WRIST] += 360
  elif wrist == "cw" and out[Axis.WRIST] > 0:
    out[Axis.WRIST] -= 360
  return out


class PreciseFlexArmBackend(OrientableGripperArmBackend, HasJoints, CanFreedrive, ABC):
  """Backend for the PreciseFlex robotic arm.

  Default to using Cartesian coordinates; some methods in Brook's TCS
  don't work with Joint coordinates.

  Documentation and error codes available at
  https://www2.brooksautomation.com/#Root/Welcome.htm
  """

  def __init__(
    self,
    driver: PreciseFlexDriver,
    gripper_length: float,
    gripper_z_offset: float,
    closed_gripper_position: float,
    is_dual_gripper: bool = False,
    has_rail: bool = False,
    read_kinematics_from_device: bool = True,
    recover_out_of_range_at_setup: bool = True,
  ) -> None:
    """
    Args:
      gripper_length: wrist-axis → TCP distance in mm. Used as the fallback /
        override; when ``read_kinematics_from_device`` is True (the default) the
        link lengths and tool length are read from the controller at setup and
        this value is only used if that read fails.
      gripper_z_offset: vertical offset in mm from the wrist plate to the tool tip.
        Depends on the mounted gripper; the concrete Device wrapper supplies a
        model-appropriate default. Always taken from here (not on the controller).
      read_kinematics_from_device: when True, read l1/l2 and the tool length from
        the controller at setup and use them for kinematics; the constructor's
        ``gripper_length`` then acts only as a fallback. Set False to force the
        constructor values regardless of what the controller reports.
      recover_out_of_range_at_setup: when True (the default), setup tries to drive a
        small out-of-range excursion back inside the soft limits (slow single-axis move
        toward in-range) via ``recover_axes_within_limits``. Set False to skip that.
        Either way, setup raises if any axis is still out of range afterward, since the
        controller would otherwise reject every commanded move (-1012).
      closed_gripper_position: firmware-unit value (passed to ``GripClosePos`` /
        ``GripOpenPos``) at which the jaws are at :attr:`min_gripper_width`.
        Depends on the mounted gripper. The conversion mm → firmware units is
        linear with slope 1: ``units = closed_gripper_position + (width_mm -
        min_gripper_width)``.
    """
    super().__init__()
    self.driver = driver
    self.profile_index: int = 1
    self.location_index: int = 1
    self._rail_position_index = 1
    self.horizontal_compliance: bool = False
    self.horizontal_compliance_torque: int = 0
    self._has_rail = has_rail
    self._is_dual_gripper = is_dual_gripper
    self.closed_gripper_position = closed_gripper_position
    self._kinematics_params = kinematics.PF400Params(
      gripper_length=gripper_length, gripper_z_offset=gripper_z_offset
    )
    self._read_kinematics_from_device = read_kinematics_from_device
    self._recover_out_of_range_at_setup = recover_out_of_range_at_setup
    # Device configuration, resolved once at setup; None until then.
    self._configuration: Optional[PreciseFlexConfiguration] = None
    if is_dual_gripper:
      warnings.warn(
        "Dual gripper support is experimental and may not work as expected.", UserWarning
      )

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    await super()._on_setup(backend_params=backend_params)
    await self.stop_freedrive_mode()
    # Resolve the device configuration once and adopt it as the source of truth;
    # without it the class defaults stay in place.
    try:
      self._configuration = await self._request_configuration()
    except Exception as exc:  # discovery is best-effort
      logger.warning(
        "[PreciseFlex %s] could not read configuration, using defaults: %s",
        self.driver.io._host,
        exc,
      )
      return
    self._adopt_configuration(self._configuration)
    self._log_configuration_summary(self._configuration)
    self._assess_configuration(self._configuration)
    await self._handle_out_of_range_axes()

  def _adopt_configuration(self, config: "PreciseFlexConfiguration") -> None:
    """Adopt the discovered configuration as the source of truth for later commands.

    The gripper width limits come from the gripper-axis soft limits, IK/FK use the
    device link lengths, and the rail / dual-gripper command paths follow the axes
    the controller actually reports.
    """
    gmin, gmax = config.gripper_width_range
    self._gripper_soft_min, self._gripper_soft_max = gmin, gmax
    self.min_gripper_width, self.max_gripper_width = gmin, gmax
    self._kinematics_params = config.kinematics
    self._has_rail = config.has_rail
    self._is_dual_gripper = config.is_dual_gripper

  def _log_configuration_summary(self, config: "PreciseFlexConfiguration") -> None:
    """Log a single structured summary of the discovered device: name, connection,
    firmware, this unit's configuration, and the resulting capabilities."""
    io = self.driver.io
    axes = f"{config.num_axes} axes" + (" + rail" if config.has_rail else "")
    grippers = [
      label
      for present, label in (
        (config.is_dual_gripper, "dual gripper"),
        (config.is_vision_gripper, "vision gripper"),
      )
      if present
    ]
    gripper_note = (", " + ", ".join(grippers)) if grippers else ""
    logger.info(
      "[%s] Connected on %s:%s\n"
      "  Firmware: GPL %s, TCS %s\n"
      "  Configuration: %s, robot_type %s, %s%s\n"
      "  Capabilities: %s reach (l1=%.1f, l2=%.1f mm), modules: %s",
      config.robot_name or config.controller_model or "PreciseFlex",
      io._host,
      io._port,
      config.gpl_version,
      config.tcs_version,
      config.controller_model,
      config.robot_type,
      axes,
      gripper_note,
      config.reach_class,
      config.kinematics.l1,
      config.kinematics.l2,
      ", ".join(config.modules),
    )

  async def _is_robot_homed(self) -> bool:
    """Whether all axes are homed (DataID 2800).

    Homing is lost on every power cycle (incremental encoders), and until it is redone
    the controller blocks commanded motion (-1021) and reports unreliable positions.
    """
    return _parse_scalar(await self.request_parameter(DataID.ROBOT_HOMED)) == 1.0

  async def _handle_out_of_range_axes(self) -> None:
    """Warn about every out-of-range axis, then correct what is recoverable, or raise.

    An axis parked outside its soft limit makes the arm unusable - the controller rejects
    every commanded move with -1012. Setup logs the full set first (either way), then, with
    ``recover_out_of_range_at_setup`` on (the default), drives each recoverable offender back
    into range. If recovery is off or leaves any axis out, setup raises with explicit
    recovery steps rather than leaving a dead arm.

    No-op until the robot is homed: an unhomed incremental axis reads a meaningless ~0
    (so the check would false-positive), and the controller blocks the recovery move with
    -1021 anyway. Homing is the prerequisite, so the check waits for it.
    """
    if not await self._is_robot_homed():
      logger.warning(
        "[PreciseFlex %s] robot not homed; skipping the out-of-range check until it is "
        "(home() first - unhomed positions are unreliable and commanded moves are blocked).",
        self.driver.io._host,
      )
      return

    def fmt(axes: Dict[Axis, tuple]) -> str:
      return "; ".join(
        f"{axis.name} at {value} (soft limit {limit})" for axis, (value, limit) in axes.items()
      )

    outside = self._axes_outside_soft_limits(await self.request_joint_position())
    if not outside:
      return
    logger.warning(
      "[PreciseFlex %s] axes out of soft limit at setup: %s", self.driver.io._host, fmt(outside)
    )
    if self._recover_out_of_range_at_setup:
      await self.recover_axes_within_limits()
      outside = self._axes_outside_soft_limits(await self.request_joint_position())
    if outside:
      raise PreciseFlexError(
        -1012,
        f"axis outside its soft limit after setup: {fmt(outside)}. The controller rejects all "
        f"commanded moves in this state. Recover with recover_axes_within_limits(), or freedrive "
        f"the axis back into range manually (required for the wrist, or when an axis is far past "
        f"its limit).",
      )

  def _assess_configuration(self, config: "PreciseFlexConfiguration") -> None:
    """Warn about an unsupported model, a missing TCS module, or an untested combo.

    The kinematics is the PreciseFlex 400 geometry, so a different model would get
    wrong joint targets; a missing module (e.g. PARobot) is the usual ``-2805``
    cause; an unlisted full configuration is allowed but flagged for reporting.
    """
    host = self.driver.io._host
    if not is_supported_model(config.robot_type):
      logger.warning(
        "[PreciseFlex %s] robot_type %s is not a model this driver's kinematics "
        "supports (%s); move_to/work_envelope may be wrong.",
        host,
        config.robot_type,
        ", ".join(SUPPORTED_ROBOT_TYPES.values()),
      )
    for module, provides, project in missing_required_modules(config.modules):
      logger.warning(
        "[PreciseFlex %s] the '%s' module (%s) is not loaded; install the '%s' TCS "
        "project (obtain it from Brooks Automation) and restart it.",
        host,
        module,
        provides,
        project,
      )
    if not is_confirmed(config.robot_type, config.gpl_version, config.tcs_version, config.modules):
      logger.info(
        "[PreciseFlex %s] this software stack has not been tested with this driver. "
        "If the arm works correctly, please add the following entry to "
        "CONFIRMED_FIRMWARE_VERSIONS in pylabrobot/brooks/confirmed_firmware_versions.py "
        "and open a pull request so other users benefit:\n%s",
        host,
        suggest_entry(config.robot_type, config.gpl_version, config.tcs_version, config.modules),
      )

  async def _request_state(
    self,
  ) -> tuple[JointPose, PreciseFlexCartesianPose]:
    """Single-query snapshot of joint state and the derived Cartesian pose."""
    joints = await self.request_joint_position()
    pose = kinematics.fk(joints, self._kinematics_params)
    # PF400 gripper stays level: pitch=90, roll=-180.
    pose = dataclasses.replace(pose, rotation=Rotation(x=-180, y=90, z=pose.rotation.yaw))
    return joints, pose

  async def _cart_to_joints(self, cart: PreciseFlexCartesianPose) -> JointPose:
    """Convert a Cartesian location into a full joint dict using our IK.

    Any of cart.orientation, cart.wrist, and cart.rail_position left as None
    default to the current pose — picks the configuration closest to where the
    arm is now. Fetches current joint state for the gripper and rail axes so
    callers can use the result directly with `_move_j` or `_set_joint_angles`.
    """
    joints, current = await self._request_state()
    cart = dataclasses.replace(
      cart,
      orientation=current.orientation if cart.orientation is None else cart.orientation,
      wrist=current.wrist if cart.wrist is None else cart.wrist,
      rail_position=current.rail_position if cart.rail_position is None else cart.rail_position,
    )
    ik_joints = _snap_to_current(kinematics.ik(cart, p=self._kinematics_params), joints, cart.wrist)
    joints[Axis.BASE] = ik_joints[1]
    joints[Axis.SHOULDER] = ik_joints[2]
    joints[Axis.ELBOW] = ik_joints[3]
    joints[Axis.WRIST] = ik_joints[4]
    joints[Axis.RAIL] = cart.rail_position
    return joints

  # -- high-level motion API -------------------------------------------------

  async def _set_speed(self, speed_pct: float):
    """Set the speed percentage of the arm's movement (0-100)."""
    await self.set_profile_speed(self.profile_index, speed_pct)

  async def _request_speed(self) -> float:
    """Get the current speed percentage of the arm's movement."""
    return await self.request_profile_speed(self.profile_index)

  # Physical jaw range for the PF400 servoed gripper. Overridden at setup from the
  # gripper-axis soft limits (DataIDs 16078/16077, Axis.GRIPPER) when discoverable.
  min_gripper_width: float = 60.0
  max_gripper_width: float = 145.0
  # Gripper-axis soft limits (GripOpenPos/GripClosePos units), read at setup; None until then.
  _gripper_soft_min: Optional[float] = None
  _gripper_soft_max: Optional[float] = None

  def _mm_to_firmware_units(self, width_mm: float) -> float:
    """Convert a jaw width (mm) to the firmware's native position unit.

    Anchored at :attr:`closed_gripper_position`, which is the firmware value
    when the jaws are at :attr:`min_gripper_width`. Slope is 1 (1 mm = 1 unit).
    """
    return self.closed_gripper_position + (width_mm - self.min_gripper_width)

  async def move_gripper(
    self,
    width: float,
    force_sensing: bool = False,
    backend_params: Optional[BackendParams] = None,
  ):
    """Move the PreciseFlex gripper jaws.

    ``force_sensing=False`` drives to the open position (``gripper 1``);
    ``force_sensing=True`` drives to the close position with force feedback
    (``gripper 2``), which may stop short of ``width`` on contact.
    """
    logger.info(
      "[PreciseFlex %s] move_gripper: width_mm=%s force_sensing=%s",
      self.driver.io._host,
      width,
      force_sensing,
    )
    units = self._mm_to_firmware_units(width)
    if (
      self._gripper_soft_min is not None
      and self._gripper_soft_max is not None
      and not (self._gripper_soft_min <= units <= self._gripper_soft_max)
    ):
      raise ValueError(
        f"gripper width {width} mm maps to firmware units {units:.1f}, outside the gripper "
        f"axis range [{self._gripper_soft_min}, {self._gripper_soft_max}] - check "
        f"closed_gripper_position (currently {self.closed_gripper_position})."
      )
    if force_sensing:
      await self._set_grip_close_pos(units)
      await self.driver.send_command("gripper 2")
    else:
      await self._set_grip_open_pos(units)
      await self.driver.send_command("gripper 1")

  async def halt(self, backend_params: Optional[BackendParams] = None):
    """Stops the current robot immediately but leaves power on."""
    await self.driver.send_command("halt")

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Move the robot to its parking position.

    Does not include checks for collision with 3rd party obstacles inside the work volume of the robot.
    """
    await self.driver.send_command("movetosafe")

  async def move_rail(self, rail_position: float) -> None:
    """Move the rail to the specified position.

    Args:
      rail_position: Rail destination in mm.

    Raises:
      RuntimeError: If the arm does not have a rail.
    """
    if not self._has_rail:
      raise RuntimeError("This arm does not have a rail.")
    await self._set_rail_position(self._rail_position_index, rail_position)
    await self._move_rail(station_id=self._rail_position_index)

  # -- JointArmBackend interface (joint-space) --------------------------------

  @dataclass
  class PickUpParams(BackendParams):
    """PreciseFlex arm parameters for plate pickup.

    Args:
      finger_speed_pct: Finger closing speed as a percentage (0-100). Default 50.0.
      grasp_force: Grasp force in Newtons. Default 10.0.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration. Only used for Cartesian moves.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
        Only used for Cartesian moves.
    """

    finger_speed_pct: float = 50.0
    grasp_force: float = 10.0
    orientation: Optional[ElbowOrientation] = None
    wrist: Optional[Wrist] = None
    rail_position: Optional[float] = None

  async def pick_up_at_joint_position(
    self,
    position: JointPose,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified joint position."""
    logger.info(
      "[PreciseFlex %s] pick_up: joints=%s, resource_width_mm=%s",
      self.driver.io._host,
      position,
      resource_width,
    )
    if not isinstance(backend_params, self.PickUpParams):
      backend_params = PreciseFlexArmBackend.PickUpParams()
    await self._set_grasp_data(
      plate_width=resource_width,
      finger_speed_pct=backend_params.finger_speed_pct,
      grasp_force=backend_params.grasp_force,
    )
    await self._pick_plate_j(position)

  @dataclass
  class DropParams(BackendParams):
    """PreciseFlex arm parameters for plate drop.

    Args:
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration. Only used for Cartesian moves.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
        Only used for Cartesian moves.
    """

    orientation: Optional[ElbowOrientation] = None
    wrist: Optional[Wrist] = None
    rail_position: Optional[float] = None

  async def drop_at_joint_position(
    self,
    position: JointPose,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified joint position."""
    logger.info(
      "[PreciseFlex %s] drop: joints=%s, resource_width_mm=%s",
      self.driver.io._host,
      position,
      resource_width,
    )
    if not isinstance(backend_params, self.DropParams):
      backend_params = PreciseFlexArmBackend.DropParams()
    await self._place_plate_j(position)

  @dataclass
  class MoveToJointPositionParams(BackendParams):
    """PreciseFlex arm parameters for joint-space moves.

    Args:
      speed_pct: Movement speed override as a percentage (0-100). If None, uses the current speed setting.
    """

    speed_pct: Optional[float] = None

  async def move_to_joint_position(
    self,
    position: JointPose,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move the arm to the specified joint position."""
    if not isinstance(backend_params, self.MoveToJointPositionParams):
      backend_params = PreciseFlexArmBackend.MoveToJointPositionParams()
    if backend_params.speed_pct is not None:
      await self._set_speed(backend_params.speed_pct)
    current = await self.request_joint_position()
    joint_coords = {**current, **position}
    self._assert_within_soft_limits(current, joint_coords)
    await self._move_j(profile_index=self.profile_index, joint_coords=joint_coords)

  def _axes_outside_soft_limits(self, joints: JointPose) -> Dict[Axis, tuple]:
    """Axes whose value lies outside their soft limit, as ``axis -> (value, (lo, hi))``.

    Iterates the soft-limit set (keyed by :class:`Axis`) and looks each axis up in
    ``joints`` so the comparison stays Axis-typed. Empty until the configuration has
    been discovered.
    """
    if self._configuration is None:
      return {}
    outside: Dict[Axis, tuple] = {}
    for axis, (lo, hi) in self._configuration.soft_limits.items():
      value = joints.get(axis)
      if value is not None and not (lo <= value <= hi):
        outside[axis] = (value, (lo, hi))
    return outside

  def _assert_within_soft_limits(self, current: JointPose, target: JointPose) -> None:
    """Turn the controller's cryptic ``-1012`` into a clear client-side error.

    Two cases block a commanded move: an axis already parked outside its soft limit
    (the controller then rejects *every* commanded move until it is recovered), and
    a target outside its soft limit (rejected outright). Freedrive can hand-move an
    axis past a soft limit, so a taught pose can land outside the commandable
    envelope. No-op until the configuration has been discovered.
    """
    for axis, (value, limit) in self._axes_outside_soft_limits(current).items():
      raise ValueError(
        f"{axis.name} is parked at {value}, outside its soft limit {limit}; the "
        f"controller rejects commanded moves while an axis is out of range (-1012). "
        f"Homing will not recover it (the rotary axes are absolute); call "
        f"recover_axes_within_limits() to drive it back into range, then retry."
      )
    for axis, (value, limit) in self._axes_outside_soft_limits(target).items():
      raise ValueError(
        f"{axis.name} target {value} is outside its soft limit {limit}; the controller "
        f"would reject the move (-1012). Re-teach this pose within the envelope."
      )

  async def request_joint_position(
    self, backend_params: Optional[BackendParams] = None
  ) -> JointPose:
    """Get the current joint position of the arm."""
    await self.driver._wait_for_eom()
    num_tries = 2
    for _ in range(num_tries):
      data = await self.driver.send_command("wherej")
      parts = data.split()
      if len(parts) > 0:
        break
    else:
      raise PreciseFlexError(-1, "Unexpected response format from wherej command.")
    return self._parse_angles_response(parts)

  async def request_gripper_pose(
    self, backend_params: Optional[BackendParams] = None
  ) -> PreciseFlexCartesianPose:
    """Get the current pose using our kinematics model (no firmware `wherec`)."""
    _, pose = await self._request_state()
    return pose

  # -- OrientableArmBackend interface (Cartesian) -----------------------------

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified Cartesian location."""
    logger.info(
      "[PreciseFlex %s] pick_up: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self.driver.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if not isinstance(backend_params, self.PickUpParams):
      backend_params = PreciseFlexArmBackend.PickUpParams()
    if backend_params.rail_position is not None:
      await self.move_rail(backend_params.rail_position)
    elif self._has_rail:
      raise ValueError(
        "rail_position must be specified for pick_up_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(z=direction),
      orientation=backend_params.orientation,
      wrist=backend_params.wrist,
    )
    await self._set_grasp_data(
      plate_width=resource_width,
      finger_speed_pct=backend_params.finger_speed_pct,
      grasp_force=backend_params.grasp_force,
    )
    await self._pick_plate_c(cartesian_position=coords)

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified Cartesian location."""
    logger.info(
      "[PreciseFlex %s] drop: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self.driver.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if not isinstance(backend_params, self.DropParams):
      backend_params = PreciseFlexArmBackend.DropParams()
    if backend_params.rail_position is not None:
      await self.move_rail(backend_params.rail_position)
    elif self._has_rail:
      raise ValueError(
        "rail_position must be specified for drop_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(z=direction),
      orientation=backend_params.orientation,
      wrist=backend_params.wrist,
    )
    await self._place_plate_c(cartesian_position=coords)

  @dataclass
  class MoveToLocationParams(BackendParams):
    """PreciseFlex arm parameters for Cartesian-space moves.

    Args:
      speed_pct: Movement speed override as a percentage (0-100). If None, uses the current speed setting.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
    """

    speed_pct: Optional[float] = None
    orientation: Optional[ElbowOrientation] = None
    wrist: Optional[Wrist] = None
    rail_position: Optional[float] = None

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move the arm to the specified Cartesian location."""
    if not isinstance(backend_params, self.MoveToLocationParams):
      backend_params = PreciseFlexArmBackend.MoveToLocationParams()
    if backend_params.speed_pct is not None:
      await self._set_speed(backend_params.speed_pct)

    if backend_params.rail_position is not None:
      await self.move_rail(backend_params.rail_position)
    elif self._has_rail:
      raise ValueError(
        "Rail position must be specified for move_to_location when using a rail-equipped arm."
      )

    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(x=-180, y=90, z=direction),
      orientation=backend_params.orientation,
      wrist=backend_params.wrist,
    )
    joints = await self._cart_to_joints(coords)
    await self._move_j(profile_index=self.profile_index, joint_coords=joints)

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    """(Single Gripper Only) Tests if the gripper is fully closed by checking the end-of-travel sensor.

    Returns:
      For standard gripper: True if the gripper is within 2mm of fully closed, otherwise False.
    """
    if self._is_dual_gripper:
      raise ValueError("IsGripperClosed command is only valid for single gripper robots.")
    response = await self.driver.send_command("IsFullyClosed")
    return int(response) == -1

  async def are_grippers_closed(self) -> tuple[bool, bool]:
    """(Dual Gripper Only) Tests if each gripper is fully closed by checking the end-of-travel sensors."""
    if not self._is_dual_gripper:
      raise ValueError("AreGrippersClosed command is only valid for dual gripper robots.")
    response = await self.driver.send_command("IsFullyClosed")
    ret_int = int(response)
    gripper_1_closed = (ret_int & 1) != 0
    gripper_2_closed = (ret_int & 2) != 0
    return (gripper_1_closed, gripper_2_closed)

  async def start_freedrive_mode(
    self, free_axes: Optional[List[int]] = None, backend_params=None
  ) -> None:
    """Enter freedrive mode, allowing manual movement of the specified joints.

    The robot must be attached to enter free mode.

    Args:
      free_axes: List of joint indices to free. Use [0] for all axes.
    """
    if free_axes is None:
      # Default to the positioning axes that exist; include the rail only when
      # fitted - freemode on an absent axis returns -2800 on a no-rail arm. The
      # cached configuration is the source of truth for the installed axes; fall
      # back to the constructor hint before setup has resolved it.
      has_rail = self._configuration.has_rail if self._configuration is not None else self._has_rail
      free_axes = [Axis.BASE, Axis.SHOULDER, Axis.ELBOW, Axis.WRIST]
      if has_rail:
        free_axes.append(Axis.RAIL)
    for axis in free_axes:
      await self.driver.send_command(f"freemode {axis}")

  async def stop_freedrive_mode(self, backend_params=None) -> None:
    """Exit freedrive mode for all axes."""
    await self.driver.send_command("freemode -1")

  # -- internal pick/place helpers -------------------------------------------

  async def _pick_plate_j(self, joint_position: JointPose):
    """Pick a plate from the specified position using joint coordinates."""
    await self._set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail()
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    ret_code = await self.driver.send_command(
      f"pickplate {self.location_index} {horizontal_compliance_int} {self.horizontal_compliance_torque}"
    )
    if ret_code == "0":
      raise PreciseFlexError(-1, "the force-controlled gripper detected no plate present.")

  async def _place_plate_j(self, joint_position: JointPose):
    """Place a plate at the specified position using joint coordinates."""
    await self._set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail()
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    await self.driver.send_command(
      f"placeplate {self.location_index} {horizontal_compliance_int} {self.horizontal_compliance_torque}"
    )

  async def _pick_plate_c(self, cartesian_position: PreciseFlexCartesianPose):
    """Pick a plate at a Cartesian position via IK + joint-space pickplate."""
    joints = await self._cart_to_joints(cartesian_position)
    await self._pick_plate_j(joints)

  async def _place_plate_c(self, cartesian_position: PreciseFlexCartesianPose):
    """Place a plate at a Cartesian position via IK + joint-space placeplate."""
    joints = await self._cart_to_joints(cartesian_position)
    await self._place_plate_j(joints)

  async def _set_grip_detail(self):
    """Configure a default vertical station type for pick/place operations."""
    await self.driver.send_command(f"StationType {self.location_index} 1 0 100 0 10")

  # -- GENERAL COMMANDS ------------------------------------------------------

  async def request_base(self) -> tuple[float, float, float, float]:
    """Get the robot base offset.

    Returns:
      A tuple containing (x_offset, y_offset, z_offset, z_rotation)
    """
    data = await self.driver.send_command("base")
    parts = data.split()
    if len(parts) != 4:
      raise PreciseFlexError(-1, "Unexpected response format from base command.")
    return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))

  async def set_base(
    self, x_offset: float, y_offset: float, z_offset: float, z_rotation: float
  ) -> None:
    """Set the robot base offset.

    Args:
      x_offset: Base X offset
      y_offset: Base Y offset
      z_offset: Base Z offset
      z_rotation: Base Z rotation

    Note:
      The robot must be attached to set the base.
      Setting the base pauses any robot motion in progress.
    """
    await self.driver.send_command(f"base {x_offset} {y_offset} {z_offset} {z_rotation}")

  async def request_monitor_speed(self) -> int:
    """Get the global system (monitor) speed.

    Returns:
      Current monitor speed as a percentage (0-100)
    """
    response = await self.driver.send_command("mspeed")
    return int(response)

  async def set_monitor_speed(self, speed_pct: int) -> None:
    """Set the global system (monitor) speed.

    Args:
      speed_pct: Speed percentage between 0 and 100, where 100 means full speed.

    Raises:
      ValueError: If speed_pct is not between 0 and 100.
    """
    if not 0 <= speed_pct <= 100:
      raise ValueError(f"speed_pct must be between 0 and 100, got {speed_pct}")
    await self.driver.send_command(f"mspeed {speed_pct}")

  async def nop(self) -> None:
    """No operation command.

    Does nothing except return the standard reply. Can be used to see if the link
    is active or to check for exceptions.
    """
    await self.driver.send_command("nop")

  async def request_payload(self) -> int:
    """Get the payload percent value for the current robot.

    Returns:
      Current payload as a percentage of maximum (0-100)
    """
    response = await self.driver.send_command("payload")
    return int(response)

  async def set_payload(self, payload_pct: int) -> None:
    """Set the payload percent of maximum for the currently selected or attached robot.

    Args:
      payload_pct: Payload percentage from 0 to 100 indicating the percent of the maximum payload the robot is carrying.

    Raises:
      ValueError: If payload_pct is not between 0 and 100.

    Note:
      If the robot is moving, waits for the robot to stop before setting a value.
    """
    if not (0 <= payload_pct <= 100):
      raise ValueError("Payload percent must be between 0 and 100")
    await self.driver.send_command(f"payload {payload_pct}")

  async def set_parameter(
    self,
    data_id: int,
    value,
    unit_number: Optional[int] = None,
    sub_unit: Optional[int] = None,
    array_index: Optional[int] = None,
  ) -> None:
    """Change a value in the controller's parameter database.

    Args:
      data_id: DataID of parameter.
      value: New parameter value. If string, will be quoted automatically.
      unit_number: Unit number, usually the robot number (1 - N_ROB).
      sub_unit: Sub-unit, usually 0.
      array_index: Array index.

    Note:
      Updated values are not saved in flash unless a save-to-flash operation
      is performed (see DataID 901).
    """
    if unit_number is not None and sub_unit is not None and array_index is not None:
      if isinstance(value, str):
        await self.driver.send_command(
          f'pc {data_id} {unit_number} {sub_unit} {array_index} "{value}"'
        )
      else:
        await self.driver.send_command(
          f"pc {data_id} {unit_number} {sub_unit} {array_index} {value}"
        )
    else:
      if isinstance(value, str):
        await self.driver.send_command(f'pc {data_id} "{value}"')
      else:
        await self.driver.send_command(f"pc {data_id} {value}")

  async def set_axis_parameter(
    self,
    data_id: int,
    axis: Axis,
    value,
    robot_number: int = 1,
  ) -> None:
    """Change one joint's element of a per-axis parameter array (``pc``).

    Per-axis DataIDs (motor current limits, hard-stop homing envelope, joint limits)
    hold one value per joint; this writes a single joint's element and leaves the rest
    untouched. ``axis`` is the controller's 1-based array index (``Axis.GRIPPER`` -> 5),
    cast to int at the wire boundary; reads of the same DataID come back in this order
    (see ``_parse_per_axis``).

    Args:
      data_id: the per-axis DataID to change.
      axis: which joint's element to write.
      value: the new value for that element.
      robot_number: unit number, the robot (1 - N_ROB).

    Note:
      Volatile until a save-to-flash (DataID 901); a power cycle otherwise restores the
      flashed value.
    """
    await self.set_parameter(
      data_id, value, unit_number=robot_number, sub_unit=0, array_index=int(axis)
    )

  async def request_parameter(
    self,
    data_id: int,
    unit_number: Optional[int] = None,
    sub_unit: Optional[int] = None,
    array_index: Optional[int] = None,
  ) -> str:
    """Get the value of a numeric parameter database item.

    Args:
      data_id: DataID of parameter.
      unit_number: Unit number, usually the robot number (1-NROB).
      sub_unit: Sub-unit, usually 0.
      array_index: Array index.

    Returns:
      str: The numeric value of the specified database parameter.
    """
    if unit_number is not None:
      if sub_unit is not None:
        if array_index is not None:
          response = await self.driver.send_command(
            f"pd {data_id} {unit_number} {sub_unit} {array_index}"
          )
        else:
          response = await self.driver.send_command(f"pd {data_id} {unit_number} {sub_unit}")
      else:
        response = await self.driver.send_command(f"pd {data_id} {unit_number}")
    else:
      response = await self.driver.send_command(f"pd {data_id}")
    return response

  @property
  def configuration(self) -> "PreciseFlexConfiguration":
    """The device configuration resolved at setup. Raises before setup()."""
    if self._configuration is None:
      raise RuntimeError("Configuration is not available until setup() has run.")
    return self._configuration

  async def request_joint_limits(self, hard: bool = False) -> Dict[Axis, tuple[float, float]]:
    """Per-axis travel limits as {Axis: (min, max)}.

    Returns the soft limits by default; pass ``hard=True`` for the hard limits.
    """
    min_id = DataID.HARD_LIMIT_MIN if hard else DataID.SOFT_LIMIT_MIN
    max_id = DataID.HARD_LIMIT_MAX if hard else DataID.SOFT_LIMIT_MAX
    return _zip_axis_ranges(
      _parse_per_axis(await self.request_parameter(min_id)),
      _parse_per_axis(await self.request_parameter(max_id)),
    )

  async def request_reference_speed(self) -> Dict[Axis, float]:
    """Per-axis rated speed at 100%; J1/J5 in mm/s, J2-J4 in deg/s."""
    return _parse_per_axis(await self.request_parameter(DataID.REFERENCE_SPEED))

  async def request_reference_accel(self) -> Dict[Axis, float]:
    """Per-axis rated acceleration at 100%."""
    return _parse_per_axis(await self.request_parameter(DataID.REFERENCE_ACCEL))

  async def request_link_lengths(self) -> tuple[float, float]:
    """(l1, l2) SCARA link lengths in mm: shoulder->elbow, elbow->wrist."""
    per_axis = _parse_per_axis(await self.request_parameter(DataID.LINK_LENGTHS))
    return per_axis[Axis.SHOULDER], per_axis[Axis.ELBOW]

  async def request_tool_length(self) -> float:
    """Wrist->TCP distance in mm (z of the tool-offset transform)."""
    values = [float(v) for v in (await self.request_parameter(DataID.TOOL_OFFSET)).split(",")]
    return values[2]

  async def request_kinematic_parameters(self) -> "kinematics.PF400Params":
    """Build PF400Params from the controller's stored geometry.

    Link lengths and tool length come from the device; gripper_z_offset is not on
    the controller, so it is carried over from the constructor params.
    """
    l1, l2 = await self.request_link_lengths()
    return dataclasses.replace(
      self._kinematics_params,
      l1=l1,
      l2=l2,
      gripper_length=await self.request_tool_length(),
    )

  async def request_reference_cartesian_speed(self) -> float:
    """Rated Cartesian (translational) speed at 100%, in mm/s."""
    return _parse_scalar(await self.request_parameter(DataID.REFERENCE_CARTESIAN_SPEED))

  async def request_reference_cartesian_accel(self) -> float:
    """Rated Cartesian (translational) acceleration at 100%, in mm/s^2."""
    return _parse_scalar(await self.request_parameter(DataID.REFERENCE_CARTESIAN_ACCEL))

  async def request_max_speed_percent(self) -> float:
    """Global cap on the speed percentage (one value, applies to all joints)."""
    return _parse_scalar(await self.request_parameter(DataID.MAX_SPEED_PERCENT))

  async def request_max_accel_percent(self) -> float:
    """Global cap on the acceleration percentage (one value, applies to all joints)."""
    return _parse_scalar(await self.request_parameter(DataID.MAX_ACCEL_PERCENT))

  async def request_max_decel_percent(self) -> float:
    """Global cap on the deceleration percentage (one value, applies to all joints)."""
    return _parse_scalar(await self.request_parameter(DataID.MAX_DECEL_PERCENT))

  async def request_manufacturer(self) -> str:
    return (await self.request_parameter(DataID.MANUFACTURER)).strip()

  async def request_controller_model(self) -> str:
    return (await self.request_parameter(DataID.CONTROLLER_MODEL)).strip()

  async def request_hardware_version(self) -> str:
    return (await self.request_parameter(DataID.HARDWARE_VERSION)).strip()

  async def request_gpl_version(self) -> str:
    """Controller firmware/runtime version (distinct from ``request_version``, the TCS app)."""
    return (await self.request_parameter(DataID.GPL_VERSION)).strip()

  async def request_controller_serial(self) -> str:
    return (await self.request_parameter(DataID.CONTROLLER_SERIAL)).strip()

  async def request_robot_name(self) -> str:
    return (await self.request_parameter(DataID.ROBOT_NAME)).strip()

  async def request_robot_type(self) -> int:
    """Built-in kinematic model id (PF400 = 12)."""
    return int(_parse_scalar(await self.request_parameter(DataID.ROBOT_TYPE)))

  async def request_axis_count(self) -> int:
    """Number of servoed axes."""
    return int(_parse_scalar(await self.request_parameter(DataID.NUM_AXES)))

  async def request_extra_axis_count(self) -> int:
    """Number of non-servoed (extra) axes."""
    return int(_parse_scalar(await self.request_parameter(DataID.EXTRA_AXES)))

  async def request_axis_mask(self) -> int:
    """Capability/option bit field (rail, dual gripper, ...)."""
    return int(_parse_scalar(await self.request_parameter(DataID.AXIS_MASK)))

  async def request_power_state(self) -> int:
    """Power / auto-execute state word."""
    return int(_parse_scalar(await self.request_parameter(DataID.POWER_STATE)))

  async def _request_configuration(self) -> "PreciseFlexConfiguration":
    """Read the controller's identity, axes, limits, kinematics, and envelope.

    Read-only (no motion, no homing required), so it is safe to call at setup.
    Link lengths and tool length are read from the controller; per-arm flags are
    derived from the joint set, the axis mask, and the model name.
    """
    soft_limits = await self.request_joint_limits()
    axis_mask = await self.request_axis_mask()
    robot_name = await self.request_robot_name()
    name_tokens = robot_name.split()
    suffix = name_tokens[-1].upper().lstrip("0123456789") if name_tokens else ""
    # The version command reports the TCS app version then its loaded modules.
    tcs_version, *modules = (seg.strip() for seg in (await self.request_version()).split(","))

    # Combine the per-axis 100% references with the global percent caps into the
    # effective per-joint maxima, so consumers get usable limits, not raw factors.
    reference_speed = await self.request_reference_speed()
    reference_accel = await self.request_reference_accel()
    speed_pct = await self.request_max_speed_percent()
    accel_pct = await self.request_max_accel_percent()
    decel_pct = await self.request_max_decel_percent()

    # Kinematics: read the link/tool geometry from the controller by default, so
    # the driver is correct for whichever 400 variant is plugged in; fall back to
    # the constructor params if the read fails or the override is set.
    kinematics_source: Literal["device", "provided", "default"]
    if self._read_kinematics_from_device:
      try:
        kinematic_params = await self.request_kinematic_parameters()
        kinematics_source = "device"
      except Exception as exc:
        logger.warning(
          "[PreciseFlex %s] could not read kinematics, using constructor params: %s",
          self.driver.io._host,
          exc,
        )
        kinematic_params = self._kinematics_params
        kinematics_source = "default"
    else:
      kinematic_params = self._kinematics_params
      kinematics_source = "provided"
    reach_class = kinematics._classify_pf400_reach((kinematic_params.l1, kinematic_params.l2))
    if reach_class == "unknown":
      logger.warning(
        "[PreciseFlex %s] link lengths l1=%.1f l2=%.1f match neither the standard %s nor "
        "extended %s PF400 arm; the arm's device-stored link lengths may have been changed",
        self.driver.io._host,
        kinematic_params.l1,
        kinematic_params.l2,
        kinematics.ARM_LINKS_STANDARD,
        kinematics.ARM_LINKS_EXTENDED,
      )

    return PreciseFlexConfiguration(
      manufacturer=await self.request_manufacturer(),
      controller_model=await self.request_controller_model(),
      hardware_version=await self.request_hardware_version(),
      gpl_version=await self.request_gpl_version(),
      controller_serial=await self.request_controller_serial(),
      robot_name=robot_name,
      robot_type=await self.request_robot_type(),
      tcs_version=tcs_version,
      modules=tuple(modules),
      num_axes=await self.request_axis_count(),
      extra_axes=await self.request_extra_axis_count(),
      axis_mask=axis_mask,
      soft_limits=soft_limits,
      hard_limits=await self.request_joint_limits(hard=True),
      max_joint_speed={a: v * speed_pct / 100 for a, v in reference_speed.items()},
      max_joint_accel={a: v * accel_pct / 100 for a, v in reference_accel.items()},
      max_joint_decel={a: v * decel_pct / 100 for a, v in reference_accel.items()},
      max_cartesian_speed=(await self.request_reference_cartesian_speed()) * speed_pct / 100,
      max_cartesian_accel=(await self.request_reference_cartesian_accel()) * accel_pct / 100,
      power_state=await self.request_power_state(),
      kinematics=kinematic_params,
      kinematics_source=kinematics_source,
      has_rail=Axis.RAIL in soft_limits,
      is_dual_gripper=bool(axis_mask & 0x80),
      is_vision_gripper=suffix[:1] == "V",
      reach_class=reach_class,
    )

  async def reset(self, robot_number: int) -> None:
    """Reset the threads associated with the specified robot.

    Stops and restarts the threads for the specified robot. Any TCP/IP connections
    made by these threads are broken. This command can only be sent to the status thread.

    Args:
      robot_number: The number of the robot thread to reset, from 1 to N_ROB. Must not be zero.

    Raises:
      ValueError: If robot_number is zero or negative.
    """
    if robot_number <= 0:
      raise ValueError("Robot number must be greater than zero")
    await self.driver.send_command(f"reset {robot_number}")

  async def request_selected_robot(self) -> int:
    """Get the number of the currently selected robot.

    Returns:
      The number of the currently selected robot.
    """
    response = await self.driver.send_command("selectRobot")
    return int(response)

  async def select_robot(self, robot_number: int) -> None:
    """Change the robot associated with this communications link.

    Does not affect the operation or attachment state of the robot. The status thread
    may select any robot or 0. Except for the status thread, a robot may only be
    selected by one thread at a time.

    Args:
      robot_number: The new robot to be connected to this thread (1 to N_ROB) or 0 for none.
    """
    await self.driver.send_command(f"selectRobot {robot_number}")

  async def request_signal(self, signal_number: int) -> int:
    """Get the value of the specified digital input or output signal.

    Args:
      signal_number: The number of the digital signal to get.

    Returns:
      The current signal value.
    """
    response = await self.driver.send_command(f"sig {signal_number}")
    sig_id, sig_val = response.split()
    return int(sig_val)

  async def set_signal(self, signal_number: int, value: int) -> None:
    """Set the specified digital input or output signal.

    Args:
      signal_number: The number of the digital signal to set.
      value: The signal value to set. 0 = off, non-zero = on.
    """
    await self.driver.send_command(f"sig {signal_number} {value}")

  async def request_system_state(self) -> int:
    """Get the global system state code.

    Returns:
      The global system state code. Please see documentation for DataID 234.
    """
    response = await self.driver.send_command("sysState")
    return int(response)

  async def request_tool_transformation_values(
    self,
  ) -> tuple[float, float, float, float, float, float]:
    """Get the current tool transformation values.

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll) for the tool transformation.
    """
    data = await self.driver.send_command("tool")
    if data.startswith("tool: "):
      data = data[6:]
    parts = data.split()
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format from tool command.")
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts)
    return (x, y, z, yaw, pitch, roll)

  async def set_tool_transformation_values(
    self, x: float, y: float, z: float, yaw: float, pitch: float, roll: float
  ) -> None:
    """Set the robot tool transformation.

    The robot must be attached to set the tool. Setting the tool pauses any robot motion in progress.

    Args:
      x: Tool X coordinate.
      y: Tool Y coordinate.
      z: Tool Z coordinate.
      yaw: Tool yaw rotation.
      pitch: Tool pitch rotation.
      roll: Tool roll rotation.
    """
    await self.driver.send_command(f"tool {x} {y} {z} {yaw} {pitch} {roll}")

  async def request_version(self) -> str:
    """Get the current version of TCS and any installed plug-ins.

    Returns:
      str: The current version information.
    """
    return await self.driver.send_command("version")

  # -- LOCATION COMMANDS -----------------------------------------------------

  async def _set_joint_angles(
    self,
    location_index: int,
    joint_position: JointPose,
  ) -> None:
    """Set joint angles for stored location, handling rail configuration."""
    if self._has_rail:
      await self.driver.send_command(
        f"locAngles {location_index} "
        f"{joint_position[Axis.RAIL]} "
        f"{joint_position[Axis.BASE]} "
        f"{joint_position[Axis.SHOULDER]} "
        f"{joint_position[Axis.ELBOW]} "
        f"{joint_position[Axis.WRIST]} "
        f"{joint_position[Axis.GRIPPER]}"
      )
    else:
      await self.driver.send_command(
        f"locAngles {location_index} "
        f"{joint_position[Axis.BASE]} "
        f"{joint_position[Axis.SHOULDER]} "
        f"{joint_position[Axis.ELBOW]} "
        f"{joint_position[Axis.WRIST]} "
        f"{joint_position[Axis.GRIPPER]}"
      )

  async def dest_c(self, arg1: int = 0) -> tuple[float, float, float, float, float, float, int]:
    """Get the destination or current Cartesian location of the robot.

    Args:
      arg1: Selects return value. Defaults to 0.
      0 = Return current Cartesian location if robot is not moving
      1 = Return target Cartesian location of the previous or current move

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll, config)
      If arg1 = 1 or robot is moving, returns the target location.
      If arg1 = 0 and robot is not moving, returns the current location.
    """
    if arg1 == 0:
      data = await self.driver.send_command("destC")
    else:
      data = await self.driver.send_command(f"destC {arg1}")
    parts = data.split()
    if len(parts) != 7:
      raise PreciseFlexError(-1, "Unexpected response format from destC command.")
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts[:6])
    config = int(parts[6])
    return (x, y, z, yaw, pitch, roll, config)

  async def dest_j(self, arg1: int = 0) -> JointPose:
    """Get the destination or current joint location of the robot.

    Args:
      arg1: Selects return value. Defaults to 0.
      0 = Return current joint location if robot is not moving
      1 = Return target joint location of the previous or current move

    Returns:
      A dict mapping Axis to float values.
      If arg1 = 1 or robot is moving, returns the target joint positions.
      If arg1 = 0 and robot is not moving, returns the current joint positions.
    """
    if arg1 == 0:
      data = await self.driver.send_command("destJ")
    else:
      data = await self.driver.send_command(f"destJ {arg1}")
    parts = data.split()
    if not parts:
      raise PreciseFlexError(-1, "Unexpected response format from destJ command.")
    return self._parse_angles_response(parts)

  async def here_j(self, location_index: int) -> None:
    """Record the current position of the selected robot into the specified Location as angles.

    The Location is automatically set to type "angles".

    Args:
      location_index: The station index, from 1 to N_LOC.
    """
    await self.driver.send_command(f"hereJ {location_index}")

  async def here_c(self, location_index: int) -> None:
    """Record the current position of the selected robot into the specified Location as Cartesian.

    The Location object is automatically set to type "Cartesian".
    Can be used to change the pallet origin (index 1,1,1) value.

    Args:
      location_index: The station index, from 1 to N_LOC.
    """
    await self.driver.send_command(f"hereC {location_index}")

  # -- PROFILE COMMANDS ------------------------------------------------------

  async def request_profile_speed(self, profile_index: int) -> float:
    """Get the speed property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed as a percentage. 100 = full speed.
    """
    response = await self.driver.send_command(f"Speed {profile_index}")
    profile, speed = response.split()
    return float(speed)

  async def set_profile_speed(self, profile_index: int, speed_pct: float) -> None:
    """Set the speed property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed_pct: The new speed as a percentage (0-100). 100 = full speed.

    Raises:
      ValueError: If speed_pct is not between 0 and 100.
    """
    if not 0 <= speed_pct <= 100:
      raise ValueError(f"speed_pct must be between 0 and 100, got {speed_pct}")
    await self.driver.send_command(f"Speed {profile_index} {speed_pct}")

  async def request_profile_speed2(self, profile_index: int) -> float:
    """Get the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed2 as a percentage. Used for Cartesian moves.
    """
    response = await self.driver.send_command(f"Speed2 {profile_index}")
    profile, speed2 = response.split()
    return float(speed2)

  async def set_profile_speed2(self, profile_index: int, speed2_pct: float) -> None:
    """Set the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed2_pct: The new speed2 as a percentage (0-100). 100 = full speed.
        Used for Cartesian moves. Normally set to 0.

    Raises:
      ValueError: If speed2_pct is not between 0 and 100.
    """
    if not 0 <= speed2_pct <= 100:
      raise ValueError(f"speed2_pct must be between 0 and 100, got {speed2_pct}")
    await self.driver.send_command(f"Speed2 {profile_index} {speed2_pct}")

  async def request_profile_accel(self, profile_index: int) -> float:
    """Get the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration as a percentage. 100 = maximum acceleration.
    """
    response = await self.driver.send_command(f"Accel {profile_index}")
    profile, accel = response.split()
    return float(accel)

  async def set_profile_accel(self, profile_index: int, acceleration_pct: float) -> None:
    """Set the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      acceleration_pct: The new acceleration as a percentage (0-100). 100 = maximum acceleration.

    Raises:
      ValueError: If acceleration_pct is not between 0 and 100.
    """
    if not 0 <= acceleration_pct <= 100:
      raise ValueError(f"acceleration_pct must be between 0 and 100, got {acceleration_pct}")
    await self.driver.send_command(f"Accel {profile_index} {acceleration_pct}")

  async def request_profile_accel_ramp(self, profile_index: int) -> float:
    """Get the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration ramp time in seconds.
    """
    response = await self.driver.send_command(f"AccRamp {profile_index}")
    profile, accel_ramp = response.split()
    return float(accel_ramp)

  async def set_profile_accel_ramp(self, profile_index: int, accel_ramp_seconds: float) -> None:
    """Set the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      accel_ramp_seconds: The new acceleration ramp time in seconds.
    """
    await self.driver.send_command(f"AccRamp {profile_index} {accel_ramp_seconds}")

  async def request_profile_decel(self, profile_index: int) -> float:
    """Get the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration as a percentage. 100 = maximum deceleration.
    """
    response = await self.driver.send_command(f"Decel {profile_index}")
    profile, decel = response.split()
    return float(decel)

  async def set_profile_decel(self, profile_index: int, deceleration_pct: float) -> None:
    """Set the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      deceleration_pct: The new deceleration as a percentage (0-100). 100 = maximum deceleration.

    Raises:
      ValueError: If deceleration_pct is not between 0 and 100.
    """
    if not 0 <= deceleration_pct <= 100:
      raise ValueError(f"deceleration_pct must be between 0 and 100, got {deceleration_pct}")
    await self.driver.send_command(f"Decel {profile_index} {deceleration_pct}")

  async def request_profile_decel_ramp(self, profile_index: int) -> float:
    """Get the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration ramp time in seconds.
    """
    response = await self.driver.send_command(f"DecRamp {profile_index}")
    profile, decel_ramp = response.split()
    return float(decel_ramp)

  async def set_profile_decel_ramp(self, profile_index: int, decel_ramp_seconds: float) -> None:
    """Set the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      decel_ramp_seconds: The new deceleration ramp time in seconds.
    """
    await self.driver.send_command(f"DecRamp {profile_index} {decel_ramp_seconds}")

  async def request_profile_in_range(self, profile_index: int) -> float:
    """Get the InRange property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current InRange value (-1 to 100).
      -1 = do not stop at end of motion if blending is possible
      0 = always stop but do not check end point error
      > 0 = wait until close to end point (larger numbers mean less position error allowed)
    """
    response = await self.driver.send_command(f"InRange {profile_index}")
    profile, in_range = response.split()
    return float(in_range)

  async def set_profile_in_range(self, profile_index: int, in_range_value: float) -> None:
    """Set the InRange property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      in_range_value: The new InRange value from -1 to 100.
      -1 = do not stop at end of motion if blending is possible
      0 = always stop but do not check end point error
      > 0 = wait until close to end point (larger numbers mean less position error allowed)

    Raises:
      ValueError: If in_range_value is not between -1 and 100.
    """
    if not (-1 <= in_range_value <= 100):
      raise ValueError("InRange value must be between -1 and 100")
    await self.driver.send_command(f"InRange {profile_index} {in_range_value}")

  async def request_profile_straight(self, profile_index: int) -> bool:
    """Get the Straight property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      The current Straight property value.
      True = follow a straight-line path
      False = follow a joint-based path (coordinated axes movement)
    """
    response = await self.driver.send_command(f"Straight {profile_index}")
    profile, straight = response.split()
    return straight == "True"

  async def set_profile_straight(self, profile_index: int, straight_mode: bool) -> None:
    """Set the Straight property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      straight_mode: The path type to use.
      True = follow a straight-line path
      False = follow a joint-based path (robot axes move in coordinated manner)

    Raises:
      ValueError: If straight_mode is not True or False.
    """
    straight_int = 1 if straight_mode else 0
    await self.driver.send_command(f"Straight {profile_index} {straight_int}")

  async def set_motion_profile_values(
    self,
    profile: int,
    speed_pct: float,
    speed2_pct: float,
    acceleration_pct: float,
    deceleration_pct: float,
    acceleration_ramp: float,
    deceleration_ramp: float,
    in_range: float,
    straight: bool,
  ):
    """
    Set motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to set values for.
      speed_pct: Percentage of maximum speed (0-100). 100 = full speed.
      speed2_pct: Secondary speed setting (0-100), typically for Cartesian moves. Normally 0.
      acceleration_pct: Percentage of maximum acceleration (0-100). 100 = full accel.
      deceleration_pct: Percentage of maximum deceleration (0-100). 100 = full decel.
      acceleration_ramp: Acceleration ramp time in seconds.
      deceleration_ramp: Deceleration ramp time in seconds.
      in_range: InRange value, from -1 to 100. -1 = allow blending, 0 = stop without checking, >0 = enforce position accuracy.
      straight: If True, follow a straight-line path (-1). If False, follow a joint-based path (0).
    """
    if not 0 <= speed_pct <= 100:
      raise ValueError(f"speed_pct must be between 0 and 100, got {speed_pct}")
    if not 0 <= speed2_pct <= 100:
      raise ValueError(f"speed2_pct must be between 0 and 100, got {speed2_pct}")
    if not 0 <= acceleration_pct <= 100:
      raise ValueError(f"acceleration_pct must be between 0 and 100, got {acceleration_pct}")
    if not 0 <= deceleration_pct <= 100:
      raise ValueError(f"deceleration_pct must be between 0 and 100, got {deceleration_pct}")
    if acceleration_ramp < 0:
      raise ValueError("acceleration_ramp must be >= 0 (seconds).")
    if deceleration_ramp < 0:
      raise ValueError("deceleration_ramp must be >= 0 (seconds).")
    if not (-1 <= in_range <= 100):
      raise ValueError("InRange must be between -1 and 100.")
    straight_int = -1 if straight else 0
    await self.driver.send_command(
      f"Profile {profile} {speed_pct} {speed2_pct} {acceleration_pct} {deceleration_pct} "
      f"{acceleration_ramp} {deceleration_ramp} {in_range} {straight_int}"
    )

  async def request_motion_profile_values(
    self, profile: int
  ) -> tuple[int, float, float, float, float, float, float, float, bool]:
    """
    Get the current motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to get values for.

    Returns:
      A tuple containing (profile, speed, speed2, acceleration, deceleration, acceleration_ramp, deceleration_ramp, in_range, straight)
        - profile: Profile index
        - speed: Percentage of maximum speed
        - speed2: Secondary speed setting
        - acceleration: Percentage of maximum acceleration
        - deceleration: Percentage of maximum deceleration
        - acceleration_ramp: Acceleration ramp time in seconds
        - deceleration_ramp: Deceleration ramp time in seconds
        - in_range: InRange value (-1 to 100)
        - straight: True if straight-line path, False if joint-based path
    """
    data = await self.driver.send_command(f"Profile {profile}")
    parts = data.split(" ")
    if len(parts) != 9:
      raise PreciseFlexError(-1, "Unexpected response format from device.")
    return (
      int(parts[0]),
      float(parts[1]),
      float(parts[2]),
      float(parts[3]),
      float(parts[4]),
      float(parts[5]),
      float(parts[6]),
      float(parts[7]),
      int(parts[8]) != 0,
    )

  # -- RAIL COMMANDS ---------------------------------------------------------

  async def _set_rail_position(self, station_id: int, rail_position: float) -> None:
    """Set the rail position for the specified station.

    Args:
      station_id: The station index.
      rail_position: The rail position in mm.
    """
    await self.driver.send_command(f"Rail {station_id} {rail_position}")

  async def _move_rail(self, station_id: Optional[int] = None, mode: int = 1) -> None:
    """Move the rail to the position stored at the specified station.

    Args:
      station_id: The station index whose rail position to move to.
      mode: Motion mode (0 = normal).
    """
    if station_id is not None:
      await self.driver.send_command(f"MoveRail {station_id} {mode}")
    else:
      await self.driver.send_command(f"MoveRail {mode}")

  # -- MOTION COMMANDS -------------------------------------------------------

  async def _move_to_stored_location(self, location_index: int, profile_index: int) -> None:
    """Move to the location specified by the station index using the specified profile.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.driver.send_command(f"move {location_index} {profile_index}")

  async def _move_to_stored_location_appro(self, location_index: int, profile_index: int) -> None:
    """Approach the location specified by the station index using the specified profile.

    This is similar to `_move_to_stored_location` except that the Z clearance value is included.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.driver.send_command(f"moveAppro {location_index} {profile_index}")

  async def _move_j(self, profile_index: int, joint_coords: JointPose) -> None:
    """Move the robot using joint coordinates, handling rail configuration."""
    if self._has_rail:
      angles_str = (
        f"{joint_coords[Axis.BASE]} "
        f"{joint_coords[Axis.SHOULDER]} "
        f"{joint_coords[Axis.ELBOW]} "
        f"{joint_coords[Axis.WRIST]} "
        f"{joint_coords[Axis.GRIPPER]} "
        f"{joint_coords[Axis.RAIL]} "
      )
    else:
      angles_str = (
        f"{joint_coords[Axis.BASE]} "
        f"{joint_coords[Axis.SHOULDER]} "
        f"{joint_coords[Axis.ELBOW]} "
        f"{joint_coords[Axis.WRIST]} "
        f"{joint_coords[Axis.GRIPPER]}"
      )
    await self.driver.send_command(f"moveJ {profile_index} {angles_str}")

  async def _move_one_axis(self, axis: Axis, position: float) -> None:
    """Move a single axis to an absolute position (firmware ``MoveOneAxis``).

    Used for recovery: the controller blocks a normal move while an axis is out of
    range, but allows a single-axis move heading back into range. Does not wait for
    the motion to complete.
    """
    await self.driver.send_command(f"MoveOneAxis {int(axis)} {position} {self.profile_index}")

  # Axes auto-recovered when parked out of range, in a deliberately safe order: the
  # gripper jaw first (no arm motion), then the Z column (vertical clearance), then
  # the rotary links shoulder -> elbow (smallest swept volume last to first).
  # The wrist is intentionally absent: rotating it back to +/-180 can self-collide, so
  # it needs the other links first driven to minimal clearance from the origin - a
  # maneuver not implemented here. The rail (gross lateral travel) is likewise left out.
  # TODO: clearance-aware wrist recovery (and rail). An out-of-range wrist or rail is
  # left for the setup post-condition to raise on.
  _RECOVERY_ORDER = (Axis.GRIPPER, Axis.BASE, Axis.SHOULDER, Axis.ELBOW)

  async def recover_axes_within_limits(
    self, speed_pct: float = 20.0, max_distance: Optional[float] = 5.0
  ) -> Dict[Axis, float]:
    """Bring out-of-range axes back inside their soft limits, one axis at a time.

    While an axis is outside its soft limit the controller rejects every commanded
    coordinated move (-1012), and homing does not help on the absolute rotary axes.
    A single-axis move is the documented exception: it may move an axis toward the
    in-range region. Each recoverable offender is driven to just inside its nearest
    soft limit, slowly, waiting for each to finish, in :attr:`_RECOVERY_ORDER`.

    Args:
      speed_pct: Profile speed for the recovery moves (default 20%, deliberately slow).
      max_distance: only move an axis that is out of range by at most this much (deg
        for the rotary axes, mm for base/gripper). An axis further out is left in place:
        a large unattended single-axis sweep risks a collision, so it is left for the
        caller to recover manually (e.g. by freedriving). Pass None to move regardless.

    Returns:
      The axes moved, as ``axis -> recovered target``. Empty when nothing recoverable
      is out of range or the configuration was not discovered. The wrist and rail are
      never auto-recovered (see :attr:`_RECOVERY_ORDER`).
    """
    outside = self._axes_outside_soft_limits(await self.request_joint_position())
    if not outside:
      return {}
    prior_speed = await self._request_speed()
    await self._set_speed(speed_pct)
    recovered: Dict[Axis, float] = {}
    try:
      for axis in self._RECOVERY_ORDER:
        if axis not in outside:
          continue
        value, (lo, hi) = outside[axis]
        above = value > hi  # which limit is violated; both moves below hinge on this
        overshoot = (value - hi) if above else (lo - value)
        if max_distance is not None and overshoot > max_distance:
          continue  # too far out to move unattended; left for the post-condition to raise
        # Land just inside the violated limit, toward the in-range region. Clamp the
        # 1-unit margin to half the range so the target stays within [lo, hi] even if
        # the range is narrower than the margin (degenerate, but keeps direction sound).
        margin = min(1.0, (hi - lo) / 2.0)
        target = (hi - margin) if above else (lo + margin)
        logger.warning(
          "[PreciseFlex %s] recovering %s from %s into soft limit [%s, %s] -> %s",
          self.driver.io._host,
          axis.name,
          value,
          lo,
          hi,
          target,
        )
        await self._move_one_axis(axis, target)
        await self.driver._wait_for_eom()
        recovered[axis] = target
    finally:
      await self._set_speed(prior_speed)  # don't leave the profile at the slow recovery speed
    return recovered

  async def release_brake(self, axis: int) -> None:
    """Release the axis brake.

    Overrides the normal operation of the brake. It is important that the brake not be set
    while a motion is being performed. This feature is used to lock an axis to prevent
    motion or jitter.

    Args:
      axis: The number of the axis whose brake should be released.
    """
    await self.driver.send_command(f"releaseBrake {axis}")

  async def set_brake(self, axis: int) -> None:
    """Set the axis brake.

    Overrides the normal operation of the brake. It is important not to set a brake on an
    axis that is moving as it may damage the brake or damage the motor.

    Args:
      axis: The number of the axis whose brake should be set.
    """
    await self.driver.send_command(f"setBrake {axis}")

  async def zero_torque(self, enable: bool, axis_mask: int = 1) -> None:
    """Sets or clears zero torque mode for the selected robot.

    Individual axes may be placed into zero torque mode while the remaining axes are servoing.

    Args:
      enable: If True, enable torque mode for axes specified by axis_mask.  If False, disable torque mode for the entire robot.
      axis_mask: The bit mask specifying the axes to be placed in torque mode when enable is True.  The mask is computed by OR'ing the axis bits: 1 = axis 1, 2 = axis 2, 4 = axis 3, 8 = axis 4, etc.  Ignored when enable is False.
    """
    if enable:
      assert axis_mask > 0, "axis_mask must be greater than 0"
      await self.driver.send_command(f"zeroTorque 1 {axis_mask}")
    else:
      await self.driver.send_command("zeroTorque 0")

  # -- PAROBOT COMMANDS ------------------------------------------------------

  async def change_config(self, grip_mode: int = 0) -> None:
    """Change Robot configuration from Righty to Lefty or vice versa using customizable locations.

    Uses customizable locations to avoid hitting robot during change.
    Does not include checks for collision inside work volume of the robot.
    Can be customized by user for their work cell configuration.

    Args:
      grip_mode: Gripper control mode.
      0 = do not change gripper (default)
      1 = open gripper
      2 = close gripper
    """
    await self.driver.send_command(f"ChangeConfig {grip_mode}")

  async def change_config2(self, grip_mode: int = 0) -> None:
    """Change Robot configuration from Righty to Lefty or vice versa using algorithm.

    Uses an algorithm to avoid hitting robot during change.
    Does not include checks for collision inside work volume of the robot.
    Can be customized by user for their work cell configuration.

    Args:
      grip_mode: Gripper control mode.
      0 = do not change gripper (default)
      1 = open gripper
      2 = close gripper
    """
    await self.driver.send_command(f"ChangeConfig2 {grip_mode}")

  async def _request_grasp_data(self) -> tuple[float, float, float]:
    """Get the data to be used for the next force-controlled PickPlate command grip operation.

    Returns:
      A tuple containing (plate_width_mm, finger_speed_pct, grasp_force)
    """
    data = await self.driver.send_command("GraspData")
    parts = data.split()
    if len(parts) != 3:
      raise PreciseFlexError(-1, "Unexpected response format from GraspData command.")
    return (float(parts[0]), float(parts[1]), float(parts[2]))

  async def _set_grasp_data(
    self, plate_width: float, finger_speed_pct: float, grasp_force: float
  ) -> None:
    """Set the data to be used for the next force-controlled PickPlate command grip operation.

    This data remains in effect until the next GraspData command or the system is restarted.

    Args:
      plate_width: The plate width in mm.
      finger_speed_pct: The finger speed during grasp as a percentage (0-100). 100 = full speed.
      grasp_force: The gripper squeezing force, in Newtons.
      A positive value indicates the fingers must close to grasp.
      A negative value indicates the fingers must open to grasp.

    Raises:
      ValueError: If finger_speed_pct is not between 0 and 100.
    """
    if not 0 <= finger_speed_pct <= 100:
      raise ValueError(f"finger_speed_pct must be between 0 and 100, got {finger_speed_pct}")
    await self.driver.send_command(f"GraspData {plate_width} {finger_speed_pct} {grasp_force}")

  async def _request_grip_close_pos(self) -> float:
    """Get the gripper close position for the servoed gripper.

    Returns:
      float: The current gripper close position.
    """
    data = await self.driver.send_command("GripClosePos")
    return float(data)

  async def _set_grip_close_pos(self, close_position: float) -> None:
    """Set the gripper close position for the servoed gripper.

    The close position may be changed by a force-controlled grip operation.

    Args:
      close_position: The new gripper close position.
    """
    await self.driver.send_command(f"GripClosePos {close_position}")

  async def _request_grip_open_pos(self) -> float:
    """Get the gripper open position for the servoed gripper.

    Returns:
      float: The current gripper open position.
    """
    data = await self.driver.send_command("GripOpenPos")
    return float(data)

  async def _set_grip_open_pos(self, open_position: float) -> None:
    """Set the gripper open position for the servoed gripper.

    Args:
      open_position: The new gripper open position.
    """
    await self.driver.send_command(f"GripOpenPos {open_position}")

  # -- parsing helpers -------------------------------------------------------

  def _parse_xyz_response(
    self, parts: List[str]
  ) -> tuple[float, float, float, float, float, float]:
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format for Cartesian coordinates.")
    return (
      float(parts[0]),
      float(parts[1]),
      float(parts[2]),
      float(parts[3]),
      float(parts[4]),
      float(parts[5]),
    )

  def _parse_angles_response(self, parts: List[str]) -> JointPose:
    """Parse angle values from a response string.

    For self._has_rail=True:  wire order is [base, shoulder, elbow, wrist, gripper, rail]
    For self._has_rail=False: wire order is [base, shoulder, elbow, wrist, gripper]
    """
    if len(parts) < 3:
      raise PreciseFlexError(-1, "Unexpected response format for angles.")
    if self._has_rail:
      return {
        Axis.RAIL: float(parts[5]) if len(parts) > 5 else 0.0,
        Axis.BASE: float(parts[0]),
        Axis.SHOULDER: float(parts[1]),
        Axis.ELBOW: float(parts[2]),
        Axis.WRIST: float(parts[3]) if len(parts) > 3 else 0.0,
        Axis.GRIPPER: float(parts[4]) if len(parts) > 4 else 0.0,
      }
    return {
      Axis.RAIL: 0.0,
      Axis.BASE: float(parts[0]),
      Axis.SHOULDER: float(parts[1]),
      Axis.ELBOW: float(parts[2]) if len(parts) > 2 else 0.0,
      Axis.WRIST: float(parts[3]) if len(parts) > 3 else 0.0,
      Axis.GRIPPER: float(parts[4]) if len(parts) > 4 else 0.0,
    }
