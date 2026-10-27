"""The PreciseFlex arm: its joints and how they move, driven through the controller.

Reached as `driver.arm`. Reads the joint state, moves in joint space and in Cartesian space through
one guarded path, and brings out-of-range axes back inside their soft limits.
"""

import asyncio
import contextlib
import dataclasses
import logging
import math
import sys
import time
from typing import (
  TYPE_CHECKING,
  AsyncIterator,
  Awaitable,
  Callable,
  ClassVar,
  Dict,
  List,
  Literal,
  NamedTuple,
  Optional,
  Sequence,
  Tuple,
)

from pylabrobot.events import coordinate_reference, evented_operation
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation

from ... import kinematics
from ...data_ids import DataID, _parse_per_axis, _parse_scalar, _zip_axis_ranges
from ...interrupt import halt_and_resync, halt_on_interrupt
from ...kinematics import (
  ElbowOrientation,
  JointState,
  PreciseFlexAxis,
  PreciseFlexCartesianPose,
  PreciseFlexPose,
  WorkEnvelope,
  Wrist,
)
from ...resource_model.pf400_manipulator import z_carriage_location
from ..errors import OperationInterrupted, OutOfRangeOfMotionError, PreciseFlexError
from .rail import PreciseFlexRail

if TYPE_CHECKING:
  from ..master import PreciseFlexDriver

logger = logging.getLogger(__name__)

# InRange sentinel that lets the controller blend through waypoints instead of stopping at each one.
BLEND_IN_RANGE = -1


def _snap_to_current(
  ik_joints: JointState, current: JointState, wrist: Optional[Wrist]
) -> JointState:
  """Shift each rotary joint by 360° multiples toward `current`, then re-enforce
  the wrist-sign half on J4 so the result still matches `wrist`. Avoids
  gratuitous full-turn moves when multiple IK solutions are equivalent.
  """
  out = dict(ik_joints)
  for axis in (PreciseFlexAxis.SHOULDER, PreciseFlexAxis.ELBOW, PreciseFlexAxis.WRIST):
    out[axis] += 360 * round((current[axis] - out[axis]) / 360)
  if wrist == "ccw" and out[PreciseFlexAxis.WRIST] < 0:
    out[PreciseFlexAxis.WRIST] += 360
  elif wrist == "cw" and out[PreciseFlexAxis.WRIST] > 0:
    out[PreciseFlexAxis.WRIST] -= 360
  return out


def _joint_state_reference(position: JointState) -> dict[str, float]:
  """Convert a joint state into a JSON-friendly target description."""
  return {
    (axis.name.lower() if isinstance(axis, PreciseFlexAxis) else str(axis)): float(value)
    for axis, value in position.items()
  }


def _cartesian_target_reference(
  location: Coordinate,
  direction: float,
  *,
  orientation: Optional["ElbowOrientation"] = None,
  wrist: Optional["Wrist"] = None,
  rail_position: Optional[float] = None,
) -> dict[str, object]:
  """Describe a Cartesian controller target without serializing a full pose object."""
  return {
    "location": coordinate_reference(location),
    "direction": float(direction),
    "orientation": orientation,
    "wrist": wrist,
    "rail_position": rail_position,
  }


@dataclasses.dataclass(frozen=True)
class PreciseFlexArmConfiguration:
  """The arm's facts, read at setup: limits and maxima of Z, shoulder, elbow and wrist, and its
  kinematics."""

  soft_limits: Dict[PreciseFlexAxis, tuple]
  hard_limits: Dict[PreciseFlexAxis, tuple]
  # Effective per-joint maxima (reference x the global percent cap, already applied).
  max_joint_speed: Dict[PreciseFlexAxis, float]
  max_joint_acceleration: Dict[PreciseFlexAxis, float]
  max_joint_deceleration: Dict[PreciseFlexAxis, float]
  max_cartesian_speed: float
  max_cartesian_acceleration: float
  kinematics: "kinematics.PF400Params" = dataclasses.field(default_factory=kinematics.PF400Params)
  kinematics_source: Literal["device", "provided", "default"] = "default"

  @property
  def reach_class(self) -> Literal["standard", "extended", "unknown"]:
    """Which arm the link lengths are; "unknown" if they match neither known arm."""
    return kinematics._classify_pf400_reach((self.kinematics.l1, self.kinematics.l2))

  @property
  def z_range(self) -> tuple:
    return self.soft_limits[PreciseFlexAxis.BASE]

  @property
  def work_envelope(self) -> WorkEnvelope:
    """Reachable tool-tip annulus, swept from the shoulder/elbow soft limits.

    Sweeps the two planar joints across their soft-limit range (Z held constant -
    it is an independent axis on a SCARA), takes the base->wrist radius at each
    sample, and brackets it by +/- the tool length (the wrist can orient the tool
    radially either way). This respects the joint limits rather than assuming full
    extension, so the outer radius is the real reach, not l1 + l2 + tool.
    """
    wrist_only = dataclasses.replace(self.kinematics, gripper_length=0.0)
    tool = self.kinematics.gripper_length
    sh_lo, sh_hi = self.soft_limits[PreciseFlexAxis.SHOULDER]
    el_lo, el_hi = self.soft_limits[PreciseFlexAxis.ELBOW]
    steps = 60
    outer, inner = 0.0, float("inf")
    for i in range(steps + 1):
      shoulder = sh_lo + (sh_hi - sh_lo) * i / steps
      for j in range(steps + 1):
        elbow = el_lo + (el_hi - el_lo) * j / steps
        joints: JointState = {
          PreciseFlexAxis.BASE: 0.0,
          PreciseFlexAxis.SHOULDER: shoulder,
          PreciseFlexAxis.ELBOW: elbow,
          PreciseFlexAxis.WRIST: 0.0,
          PreciseFlexAxis.GRIPPER: 0.0,
          PreciseFlexAxis.RAIL: 0.0,
        }
        wrist = kinematics.fk(joints, wrist_only).location
        radius = (wrist.x * wrist.x + wrist.y * wrist.y) ** 0.5
        outer = max(outer, radius + tool)
        inner = min(inner, abs(radius - tool))
    zmin, zmax = self.z_range
    return WorkEnvelope(inner=inner, outer=outer, zmin=zmin, zmax=zmax)


class MotionProfile(NamedTuple):
  """A controller motion profile, as reported by ``Profile <n>`` (field order matches the wire)."""

  profile: int
  speed: float
  speed2: float
  acceleration: float
  deceleration: float
  acceleration_ramp: float
  deceleration_ramp: float
  in_range: float  # -1 (BLEND_IN_RANGE) to 100; -1 blends, 0 stops, >0 enforces position accuracy
  straight: bool  # True = straight-line path, False = joint-based path


class PreciseFlexArm:
  """The arm of a PreciseFlex: its joints, read and moved through the controller.

  Reached as `driver.arm`. Sends through the driver's `send_command`; every move whose target joint
  states are known before it is sent goes through `_guarded_move`.
  """

  # What the driver sends when a caller leaves the value out. Tune one arm by assigning on the
  # instance, or every arm by assigning on the class.
  default_recovery_speed_percent: float = 20.0

  # Parked orientations: planar folds, named for the way the gripper points. Z, gripper and rail are
  # left out: ``park()`` fills Z from the travel, and a held plate or a rail is not disturbed.
  PARKING_POSITION_BACK: ClassVar[JointState] = {
    PreciseFlexAxis.SHOULDER: 90.0,
    PreciseFlexAxis.ELBOW: 180.0,
    PreciseFlexAxis.WRIST: 90.0,
  }
  PARKING_POSITION_RIGHT: ClassVar[JointState] = {
    PreciseFlexAxis.SHOULDER: 0.0,
    PreciseFlexAxis.ELBOW: 180.0,
    PreciseFlexAxis.WRIST: 180.0,
  }
  PARKING_POSITION_FRONT: ClassVar[JointState] = {
    PreciseFlexAxis.SHOULDER: -90.0,
    PreciseFlexAxis.ELBOW: 180.0,
    PreciseFlexAxis.WRIST: 270.0,
  }

  def __init__(self, driver: "PreciseFlexDriver") -> None:
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver
    self.configuration: Optional[PreciseFlexArmConfiguration] = None
    # What models the arm: the carriage on the column and the two links. None until setup hangs
    # them, which it does only for a driver given a base plate.
    self.resource: Optional[Resource] = None
    self.link_1: Optional[LinkBody] = None
    self.link_2: Optional[LinkBody] = None
    self.profile_index: int = 1
    self.station_index: int = 1
    self.horizontal_compliance: bool = False
    self.horizontal_compliance_torque: int = 0

  # -- joint state ---------------------------------------------------------------------------------

  # The order joint values go over the wire in. An arm with a rail adds the rail's.
  _WIRE_ORDER = (
    PreciseFlexAxis.BASE,
    PreciseFlexAxis.SHOULDER,
    PreciseFlexAxis.ELBOW,
    PreciseFlexAxis.WRIST,
    PreciseFlexAxis.GRIPPER,
  )

  def _parse_angles_response(self, parts: List[str]) -> JointState:
    """Parse the joint values of a reply, in `_WIRE_ORDER`, then the rail's on an arm with one.

    A value the reply leaves out reads 0.
    """
    if len(parts) < 3:
      raise PreciseFlexError(-1, "Unexpected response format for angles.")
    wire_order = (
      [*self._WIRE_ORDER, PreciseFlexAxis.RAIL] if self._driver._has_rail else self._WIRE_ORDER
    )
    joints = dict.fromkeys([PreciseFlexAxis.RAIL, *self._WIRE_ORDER], 0.0)
    joints.update(zip(wire_order, map(float, parts)))
    return joints

  async def _wait_for_eom(
    self, poll_interval: float = 0.05, settle: float = 0.02, timeout: float = 60.0
  ) -> None:
    """Wait until the arm has stopped moving, leaving the connection free.

    Polls ``wherej`` and returns once three samples in a row agree within ``settle``. Two are not
    enough: after a ``moveJ`` is accepted the arm creeps under ``settle`` for about 0.1 s (IO logs).
    An arm already stopped, also one stopped short of its target, returns at once.

    The firmware's ``waitForEom`` is not used: it makes the controller ignore everything else on the
    connection, ``halt`` included, until the move ends (hardware-verified). Every command that
    starts motion waits here first, since ``moveJ`` returns once accepted: without it a grip after
    an approach closes mid-descent.

    Raises:
      TimeoutError: if the arm never settles within ``timeout`` seconds.
      OperationInterrupted: on a user interrupt (the arm is halted and the connection kept).
    """

    def _floats(reply: str) -> list[float]:
      return [float(x) for x in reply.split()]

    # On interrupt, `halt` stops the move and the link is resynchronised and kept. It is sent
    # outside `_io_lock`: an emergency halt must not wait on another caller's read.
    async with halt_on_interrupt(lambda: halt_and_resync(self._driver.io, b"halt")):
      previous = _floats(await self._driver.send_command("wherej"))
      deadline = time.monotonic() + timeout
      still = 0
      while True:
        await asyncio.sleep(poll_interval)
        current = _floats(await self._driver.send_command("wherej"))
        still = still + 1 if all(abs(c - p) < settle for c, p in zip(current, previous)) else 0
        if still == 2:
          return  # three samples agree: stopped moving
        if time.monotonic() > deadline:
          raise TimeoutError(f"motion did not settle within {timeout:.0f}s (current={current})")
        previous = current

  def update_joint_state(self, joints: JointState) -> None:
    """Stand the resources that model the arm where every joint is.

    The carriage is stood where the Z drive is, and each member is turned about the joint it turns
    on to the angle that joint reports. Does nothing until the arm is modelled.

    Args:
      joints: the joint state, as ``request_joint_state`` returns it.
    """
    if self.resource is None or self.link_1 is None or self.link_2 is None:
      return
    self.resource.location = z_carriage_location(joints[PreciseFlexAxis.BASE])
    self.link_1.rotate_to(
      z=joints[PreciseFlexAxis.SHOULDER], pivot_coordinate=self.link_1.proximal_joint
    )
    self.link_2.rotate_to(
      z=joints[PreciseFlexAxis.ELBOW], pivot_coordinate=self.link_2.proximal_joint
    )
    gripper = self._driver.gripper.resource
    if gripper is not None:
      gripper.rotate_to(z=joints[PreciseFlexAxis.WRIST], pivot_coordinate=gripper.proximal_joint)

  async def request_joint_state(self) -> JointState:
    """Get the current joint position of the arm."""
    await self._wait_for_eom()
    num_tries = 2
    for _ in range(num_tries):
      data = await self._driver.send_command("wherej")
      parts = data.split()
      if len(parts) > 0:
        break
    else:
      raise PreciseFlexError(-1, "Unexpected response format from wherej command.")
    joints = self._parse_angles_response(parts)
    self.update_joint_state(joints)
    self._driver.gripper.update_width(
      self._driver.gripper._firmware_units_to_mm(joints[PreciseFlexAxis.GRIPPER])
    )
    return joints

  async def _request_joint_state_after_move(self) -> None:
    """Wait for the arm to stop and read where it did, on a move's success and its failure alike.

    Called from a move's ``finally``. After an accepted move a failure of the read is raised: the
    controller answers a fault during the motion (a collision, a stall) on the next command. After
    a failed move it is logged and swallowed, so it does not replace the move's own error. An
    interrupt is passed on.
    """
    # The move's own error, while this runs in its `finally`.
    move_failed = sys.exc_info()[1] is not None
    try:
      await self.request_joint_state()
    except OperationInterrupted:
      raise
    except Exception:
      if not move_failed:
        raise
      logger.warning(
        "[PreciseFlex %s] could not read where the arm stopped; its model is stale",
        self._driver.io._host,
      )

  async def request_state(self) -> str:
    """Return state of motion.

    This value indicates the state of the currently executing or last completed robot motion.
    For additional information, please see 'Robot.TrajState' in the GPL reference manual.

    Returns:
      str: The current motion state.
    """
    return await self._driver.send_command("state")

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

  async def _request_state(
    self,
  ) -> tuple[JointState, PreciseFlexCartesianPose]:
    """Single-query snapshot of joint state and the derived Cartesian pose."""
    joints = await self.request_joint_state()
    return joints, kinematics.fk(joints, self._driver._kinematics_params)

  # -- motion primitives ---------------------------------------------------------------------------

  async def _unchecked_fw_move_j(self, profile_index: int, joint_coords: JointState) -> None:
    """Move the robot using joint coordinates, handling rail configuration. Raw moveJ - the
    out-of-range guard lives in the caller (``_guarded_move``), not in this primitive."""
    angles_str = " ".join(str(joint_coords[axis]) for axis in self._WIRE_ORDER)
    if self._driver._has_rail:
      # The rail's value goes last, and the line has always ended with a space after it.
      angles_str += f" {joint_coords[PreciseFlexAxis.RAIL]} "
    await self._driver.send_command(f"moveJ {profile_index} {angles_str}")

  async def _unchecked_fw_move_one_axis(self, axis: PreciseFlexAxis, position: float) -> None:
    """Move a single axis to an absolute position (firmware ``MoveOneAxis``).

    Used for recovery: the controller blocks a normal move while an axis is out of
    range, but allows a single-axis move heading back into range. Does not wait for
    the motion to complete.
    """
    await self._driver.send_command(f"MoveOneAxis {int(axis)} {position} {self.profile_index}")

  async def _unchecked_fw_set_joint_angles(
    self,
    station_index: int,
    joint_position: JointState,
  ) -> None:
    """Set joint angles for a station, handling rail configuration."""
    angles_str = " ".join(str(joint_position[axis]) for axis in self._WIRE_ORDER)
    if self._driver._has_rail:
      # Here the rail's value goes first.
      angles_str = f"{joint_position[PreciseFlexAxis.RAIL]} {angles_str}"
    await self._driver.send_command(f"locAngles {station_index} {angles_str}")

  def _plan_cartesian_pose(
    self,
    pose: PreciseFlexCartesianPose,
    prev_joints: JointState,
    prev_pose: PreciseFlexCartesianPose,
  ) -> Tuple[JointState, PreciseFlexCartesianPose]:
    """One Cartesian pose as a joint target, from the joints and the pose before it.

    Fields the pose leaves out are taken from the pose before, so IK stays on the same branch. IK
    solves the arm axes only: the gripper keeps its value, and the rail takes the pose's on an arm
    with one.

    Returns:
      The joint target, and the pose with every field filled in.
    """
    cart = dataclasses.replace(
      pose,
      orientation=prev_pose.orientation if pose.orientation is None else pose.orientation,
      wrist=prev_pose.wrist if pose.wrist is None else pose.wrist,
      # PF400 IK expects a shoulder/reference rail position even on rail-less arms.
      rail_position=prev_pose.rail_position if pose.rail_position is None else pose.rail_position,
    )
    ik_joints = _snap_to_current(
      kinematics.ik(cart, p=self._driver._kinematics_params), prev_joints, cart.wrist
    )
    target = dict(prev_joints)
    for axis in (
      PreciseFlexAxis.BASE,
      PreciseFlexAxis.SHOULDER,
      PreciseFlexAxis.ELBOW,
      PreciseFlexAxis.WRIST,
    ):
      target[axis] = ik_joints[axis]
    if self._driver._has_rail and cart.rail_position is not None:
      target[PreciseFlexAxis.RAIL] = cart.rail_position
    return target, cart

  def _cart_to_joints(self, cart: PreciseFlexCartesianPose, joints: JointState) -> JointState:
    """Convert a Cartesian location into a full joint dict using our IK, from the joints read.

    Any of cart.orientation, cart.wrist, and cart.rail_position left as None
    default to the current pose - picks the configuration closest to where the
    arm is now. The gripper and rail axes come from `joints`, so callers get a
    complete joint dict, ready for `_guarded_move`.
    """
    current = kinematics.fk(joints, self._driver._kinematics_params)
    return self._plan_cartesian_pose(cart, joints, current)[0]

  # -- speed and motion profiles -------------------------------------------------------------------

  async def request_monitor_speed(self) -> int:
    """Get the global system (monitor) speed.

    Returns:
      Current monitor speed as a percentage (0-100)
    """
    response = await self._driver.send_command("mspeed")
    return int(response)

  async def set_monitor_speed(self, speed_percent: int) -> None:
    """Set the global system (monitor) speed.

    Args:
      speed_percent: Speed percentage between 0 and 100, where 100 means full speed.

    Raises:
      ValueError: If speed_percent is not between 0 and 100.
    """
    if not 0 <= speed_percent <= 100:
      raise ValueError(f"speed_percent must be between 0 and 100, got {speed_percent}")
    await self._driver.send_command(f"mspeed {speed_percent}")

  async def request_payload(self) -> int:
    """Get the payload percent value for the current robot.

    Returns:
      Current payload as a percentage of maximum (0-100)
    """
    response = await self._driver.send_command("payload")
    return int(response)

  async def set_payload(self, payload_percent: int) -> None:
    """Set the payload percent of maximum for the currently selected or attached robot.

    Args:
      payload_percent: Payload percentage from 0 to 100 indicating the percent of the maximum
        payload the robot is carrying.

    Raises:
      ValueError: If payload_percent is not between 0 and 100.

    Note:
      If the robot is moving, waits for the robot to stop before setting a value.
    """
    if not (0 <= payload_percent <= 100):
      raise ValueError("Payload percent must be between 0 and 100")
    await self._driver.send_command(f"payload {payload_percent}")

  async def request_profile_speed(self, profile_index: int) -> float:
    """Get the speed property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed as a percentage. 100 = full speed.
    """
    response = await self._driver.send_command(f"Speed {profile_index}")
    profile, speed = response.split()
    return float(speed)

  async def set_profile_speed(self, profile_index: int, speed_percent: float) -> None:
    """Set the speed property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed_percent: The new speed as a percentage (0-100). 100 = full speed.

    Raises:
      ValueError: If speed_percent is not between 0 and 100.
    """
    if not 0 <= speed_percent <= 100:
      raise ValueError(f"speed_percent must be between 0 and 100, got {speed_percent}")
    await self._driver.send_command(f"Speed {profile_index} {speed_percent}")

  async def request_profile_speed2(self, profile_index: int) -> float:
    """Get the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed2 as a percentage. Used for Cartesian moves.
    """
    response = await self._driver.send_command(f"Speed2 {profile_index}")
    profile, speed2 = response.split()
    return float(speed2)

  async def set_profile_speed2(self, profile_index: int, speed2_percent: float) -> None:
    """Set the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed2_percent: The new speed2 as a percentage (0-100). 100 = full speed.
        Used for Cartesian moves. Normally set to 0.

    Raises:
      ValueError: If speed2_percent is not between 0 and 100.
    """
    if not 0 <= speed2_percent <= 100:
      raise ValueError(f"speed2_percent must be between 0 and 100, got {speed2_percent}")
    await self._driver.send_command(f"Speed2 {profile_index} {speed2_percent}")

  async def request_profile_acceleration(self, profile_index: int) -> float:
    """Get the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration as a percentage. 100 = maximum acceleration.
    """
    response = await self._driver.send_command(f"Accel {profile_index}")
    profile, acceleration = response.split()
    return float(acceleration)

  async def set_profile_acceleration(self, profile_index: int, acceleration_percent: float) -> None:
    """Set the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      acceleration_percent: The new acceleration as a percentage (0-100). 100 = maximum
        acceleration.

    Raises:
      ValueError: If acceleration_percent is not between 0 and 100.
    """
    if not 0 <= acceleration_percent <= 100:
      raise ValueError(
        f"acceleration_percent must be between 0 and 100, got {acceleration_percent}"
      )
    await self._driver.send_command(f"Accel {profile_index} {acceleration_percent}")

  async def request_profile_acceleration_ramp(self, profile_index: int) -> float:
    """Get the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration ramp time in seconds.
    """
    response = await self._driver.send_command(f"AccRamp {profile_index}")
    profile, acceleration_ramp = response.split()
    return float(acceleration_ramp)

  async def set_profile_acceleration_ramp(
    self, profile_index: int, acceleration_ramp_seconds: float
  ) -> None:
    """Set the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      acceleration_ramp_seconds: The new acceleration ramp time in seconds.
    """
    await self._driver.send_command(f"AccRamp {profile_index} {acceleration_ramp_seconds}")

  async def request_profile_deceleration(self, profile_index: int) -> float:
    """Get the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration as a percentage. 100 = maximum deceleration.
    """
    response = await self._driver.send_command(f"Decel {profile_index}")
    profile, deceleration = response.split()
    return float(deceleration)

  async def set_profile_deceleration(self, profile_index: int, deceleration_percent: float) -> None:
    """Set the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      deceleration_percent: The new deceleration as a percentage (0-100). 100 = maximum
        deceleration.

    Raises:
      ValueError: If deceleration_percent is not between 0 and 100.
    """
    if not 0 <= deceleration_percent <= 100:
      raise ValueError(
        f"deceleration_percent must be between 0 and 100, got {deceleration_percent}"
      )
    await self._driver.send_command(f"Decel {profile_index} {deceleration_percent}")

  async def request_profile_deceleration_ramp(self, profile_index: int) -> float:
    """Get the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration ramp time in seconds.
    """
    response = await self._driver.send_command(f"DecRamp {profile_index}")
    profile, deceleration_ramp = response.split()
    return float(deceleration_ramp)

  async def set_profile_deceleration_ramp(
    self, profile_index: int, deceleration_ramp_seconds: float
  ) -> None:
    """Set the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      deceleration_ramp_seconds: The new deceleration ramp time in seconds.
    """
    await self._driver.send_command(f"DecRamp {profile_index} {deceleration_ramp_seconds}")

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
    response = await self._driver.send_command(f"InRange {profile_index}")
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
    await self._driver.send_command(f"InRange {profile_index} {in_range_value}")

  async def request_profile_straight(self, profile_index: int) -> bool:
    """Get the Straight property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      The current Straight property value.
      True = follow a straight-line path
      False = follow a joint-based path (coordinated axes movement)
    """
    response = await self._driver.send_command(f"Straight {profile_index}")
    profile, straight = response.split()
    return straight == "True"

  async def set_profile_straight(self, profile_index: int, straight_mode: bool) -> None:
    """Set the Straight property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      straight_mode: The path type to use.
      True = follow a straight-line path
      False = follow a joint-based path (robot axes move in coordinated manner)
    """
    straight_int = 1 if straight_mode else 0
    await self._driver.send_command(f"Straight {profile_index} {straight_int}")

  async def request_motion_profile_values(self, profile: int) -> MotionProfile:
    """
    Get the current motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to get values for.

    Returns:
      A :class:`MotionProfile` with the profile's speed, acceleration, ramps, InRange and path mode.
    """
    data = await self._driver.send_command(f"Profile {profile}")
    parts = data.split(" ")
    if len(parts) != 9:
      raise PreciseFlexError(-1, "Unexpected response format from device.")
    return MotionProfile(
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

  async def set_motion_profile_values(
    self,
    profile: int,
    speed_percent: float,
    speed2_percent: float,
    acceleration_percent: float,
    deceleration_percent: float,
    acceleration_ramp: float,
    deceleration_ramp: float,
    in_range: float,
    straight: bool,
  ):
    """
    Set motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to set values for.
      speed_percent: Percentage of maximum speed (0-100). 100 = full speed.
      speed2_percent: Secondary speed setting (0-100), typically for Cartesian moves. Normally 0.
      acceleration_percent: Percentage of maximum acceleration (0-100). 100 = full acceleration.
      deceleration_percent: Percentage of maximum deceleration (0-100). 100 = full deceleration.
      acceleration_ramp: Acceleration ramp time in seconds.
      deceleration_ramp: Deceleration ramp time in seconds.
      in_range: InRange value, from -1 to 100. -1 = allow blending, 0 = stop without checking, >0 =
        enforce position accuracy.
      straight: If True, follow a straight-line path (-1). If False, follow a joint-based path (0).
    """
    if not 0 <= speed_percent <= 100:
      raise ValueError(f"speed_percent must be between 0 and 100, got {speed_percent}")
    if not 0 <= speed2_percent <= 100:
      raise ValueError(f"speed2_percent must be between 0 and 100, got {speed2_percent}")
    if not 0 <= acceleration_percent <= 100:
      raise ValueError(
        f"acceleration_percent must be between 0 and 100, got {acceleration_percent}"
      )
    if not 0 <= deceleration_percent <= 100:
      raise ValueError(
        f"deceleration_percent must be between 0 and 100, got {deceleration_percent}"
      )
    if acceleration_ramp < 0:
      raise ValueError("acceleration_ramp must be >= 0 (seconds).")
    if deceleration_ramp < 0:
      raise ValueError("deceleration_ramp must be >= 0 (seconds).")
    if not (-1 <= in_range <= 100):
      raise ValueError("InRange must be between -1 and 100.")
    straight_int = -1 if straight else 0
    await self._driver.send_command(
      f"Profile {profile} {speed_percent} {speed2_percent} {acceleration_percent} "
      f"{deceleration_percent} {acceleration_ramp} {deceleration_ramp} {in_range} {straight_int}"
    )

  async def _set_speed(self, speed_percent: float):
    """Set the speed percentage of the arm's movement (0-100)."""
    await self.set_profile_speed(self.profile_index, speed_percent)

  async def _request_speed(self) -> float:
    """Get the current speed percentage of the arm's movement."""
    return await self.request_profile_speed(self.profile_index)

  @contextlib.asynccontextmanager
  async def at_speed(self, speed_percent: Optional[float]) -> AsyncIterator[None]:
    """Run the moves inside at their own speed, then put the profile speed back.

    The restore runs in a ``finally``, so a fault mid-move cannot leave the arm slow. None changes
    nothing, so a caller can pass a speed straight through.

    Args:
      speed_percent: Movement speed as a percentage (0-100), or None to keep the current one.
    """
    if speed_percent is None:
      yield
      return
    prior = await self._request_speed()
    await self._set_speed(speed_percent)
    try:
      yield
    finally:
      try:
        await self._set_speed(prior)
      except Exception:
        # Raising here replaces the move's own error, so say plainly what the arm is left at.
        logger.error(
          "[PreciseFlex %s] could not restore profile speed to %s; the arm is still at %s",
          self._driver.io._host,
          prior,
          speed_percent,
        )
        raise

  # -- brakes, torque and freedrive ----------------------------------------------------------------

  async def release_brake(self, axis: int) -> None:
    """Release the axis brake.

    Overrides the normal operation of the brake. It is important that the brake not be set
    while a motion is being performed. This feature is used to lock an axis to prevent
    motion or jitter.

    Args:
      axis: The number of the axis whose brake should be released.
    """
    await self._driver.send_command(f"releaseBrake {axis}")

  async def reengage_brake(self, axis: int) -> None:
    """Re-engage the axis brake.

    Overrides the normal operation of the brake. It is important not to set a brake on an
    axis that is moving as it may damage the brake or damage the motor.

    Args:
      axis: The number of the axis whose brake should be set.
    """
    await self._driver.send_command(f"setBrake {axis}")

  async def _unchecked_fw_start_zero_torque(self, axis_mask: int) -> None:
    """Place the axes of ``axis_mask`` in zero torque mode. Nothing is guarded."""
    await self._driver.send_command(f"zeroTorque 1 {axis_mask}")

  async def start_zero_torque(self, axis_mask: int = 1) -> None:
    """Place axes of the selected robot in zero torque mode.

    Individual axes may be placed into zero torque mode while the remaining axes are servoing.

    Args:
      axis_mask: The bit mask specifying the axes to be placed in torque mode. The mask is computed
        by OR'ing the axis bits: 1 = axis 1, 2 = axis 2, 4 = axis 3, 8 = axis 4, etc.

    Raises:
      ValueError: If ``axis_mask`` names no axis.
    """
    if axis_mask <= 0:
      raise ValueError(f"axis_mask must be greater than 0, is {axis_mask}")
    await self._unchecked_fw_start_zero_torque(axis_mask)

  async def stop_zero_torque(self) -> None:
    """Take the entire robot out of zero torque mode."""
    await self._driver.send_command("zeroTorque 0")

  @evented_operation(
    "precise_flex.start_freedrive",
    lambda self, free_axes=None: {
      "device": self._driver._controller_reference(),
      "free_axes": [int(axis) for axis in free_axes] if free_axes is not None else None,
    },
  )
  async def start_freedrive_mode(self, free_axes: Optional[List[int]] = None) -> None:
    """Enter freedrive mode, allowing manual movement of the specified joints.

    The robot must be attached to enter free mode.

    Args:
      free_axes: List of joint indices to free. Use [0] for all axes.
    """
    if free_axes is None:
      # The positioning axes that exist: freemode on an absent rail returns -2800.
      free_axes = [
        PreciseFlexAxis.BASE,
        PreciseFlexAxis.SHOULDER,
        PreciseFlexAxis.ELBOW,
        PreciseFlexAxis.WRIST,
      ]
      if self._driver.rail is not None:
        free_axes.append(PreciseFlexAxis.RAIL)
    for axis in free_axes:
      await self._driver.send_command(f"freemode {axis}")

  @evented_operation(
    "precise_flex.stop_freedrive",
    lambda self: {"device": self._driver._controller_reference()},
  )
  async def stop_freedrive_mode(self) -> None:
    """Exit freedrive mode for all axes."""
    await self._driver.send_command("freemode -1")

  @evented_operation(
    "precise_flex.halt",
    lambda self: {"device": self._driver._controller_reference()},
  )
  async def halt(self):
    """Stops the current robot immediately but leaves power on."""
    await self._driver.send_command("halt")

  # -- limits and kinematics -----------------------------------------------------------------------

  async def request_joint_limits(
    self, hard: bool = False
  ) -> Dict[PreciseFlexAxis, tuple[float, float]]:
    """Per-axis travel limits as {PreciseFlexAxis: (min, max)}.

    Returns the soft limits by default; pass ``hard=True`` for the hard limits.
    """
    min_id = DataID.HARD_LIMIT_MIN if hard else DataID.SOFT_LIMIT_MIN
    max_id = DataID.HARD_LIMIT_MAX if hard else DataID.SOFT_LIMIT_MAX
    return _zip_axis_ranges(
      _parse_per_axis(await self._driver.request_parameter(min_id)),
      _parse_per_axis(await self._driver.request_parameter(max_id)),
    )

  async def request_reference_speed(self) -> Dict[PreciseFlexAxis, float]:
    """Per-axis rated speed at 100%; J1/J5 in mm/s, J2-J4 in deg/s."""
    return _parse_per_axis(await self._driver.request_parameter(DataID.REFERENCE_SPEED))

  async def request_reference_acceleration(self) -> Dict[PreciseFlexAxis, float]:
    """Per-axis rated acceleration at 100%."""
    return _parse_per_axis(await self._driver.request_parameter(DataID.REFERENCE_ACCEL))

  async def request_link_lengths(self) -> tuple[float, float]:
    """(l1, l2) SCARA link lengths in mm: shoulder->elbow, elbow->wrist."""
    per_axis = _parse_per_axis(await self._driver.request_parameter(DataID.LINK_LENGTHS))
    return per_axis[PreciseFlexAxis.SHOULDER], per_axis[PreciseFlexAxis.ELBOW]

  async def request_tool_length(self) -> float:
    """Wrist->TCP distance in mm (z of the tool-offset transform)."""
    values = [
      float(v) for v in (await self._driver.request_parameter(DataID.TOOL_OFFSET)).split(",")
    ]
    return values[2]

  async def request_kinematic_parameters(self) -> "kinematics.PF400Params":
    """Build PF400Params from the controller's stored geometry.

    Link lengths and tool length come from the device; gripper_z_offset is not on
    the controller, so it is carried over from the constructor params.
    """
    l1, l2 = await self.request_link_lengths()
    return dataclasses.replace(
      self._driver._kinematics_params,
      l1=l1,
      l2=l2,
      gripper_length=await self.request_tool_length(),
    )

  async def request_reference_cartesian_speed(self) -> float:
    """Rated Cartesian (translational) speed at 100%, in mm/s."""
    return _parse_scalar(await self._driver.request_parameter(DataID.REFERENCE_CARTESIAN_SPEED))

  async def request_reference_cartesian_acceleration(self) -> float:
    """Rated Cartesian (translational) acceleration at 100%, in mm/s^2."""
    return _parse_scalar(await self._driver.request_parameter(DataID.REFERENCE_CARTESIAN_ACCEL))

  async def request_max_speed_percent(self) -> float:
    """Global cap on the speed percentage (one value, applies to all joints)."""
    return _parse_scalar(await self._driver.request_parameter(DataID.MAX_SPEED_PERCENT))

  async def request_max_acceleration_percent(self) -> float:
    """Global cap on the acceleration percentage (one value, applies to all joints)."""
    return _parse_scalar(await self._driver.request_parameter(DataID.MAX_ACCEL_PERCENT))

  async def request_max_deceleration_percent(self) -> float:
    """Global cap on the deceleration percentage (one value, applies to all joints)."""
    return _parse_scalar(await self._driver.request_parameter(DataID.MAX_DECEL_PERCENT))

  # -- base and tool frames ------------------------------------------------------------------------

  async def request_base(self) -> tuple[float, float, float, float]:
    """Get the robot base offset.

    Returns:
      A tuple containing (x_offset, y_offset, z_offset, z_rotation)
    """
    data = await self._driver.send_command("base")
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
    await self._driver.send_command(f"base {x_offset} {y_offset} {z_offset} {z_rotation}")

  async def request_tool_transformation_values(
    self,
  ) -> tuple[float, float, float, float, float, float]:
    """Get the current tool transformation values.

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll) for the tool transformation.
    """
    data = await self._driver.send_command("tool")
    if data.startswith("tool: "):
      data = data[6:]
    parts = data.split()
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format from tool command.")
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts)
    return (x, y, z, yaw, pitch, roll)

  async def _set_tool_transformation_values(
    self, x: float, y: float, z: float, yaw: float, pitch: float, roll: float
  ) -> None:
    """Set the tool transformation. The robot must be attached; motion in progress pauses.

    Private: the kinematics read the tool once at setup, so changing it leaves
    `request_gripper_pose` out of step with the controller until the configuration is read again.

    Args:
      x: Tool X coordinate.
      y: Tool Y coordinate.
      z: Tool Z coordinate.
      yaw: Tool yaw rotation.
      pitch: Tool pitch rotation.
      roll: Tool roll rotation.
    """
    await self._driver.send_command(f"tool {x} {y} {z} {yaw} {pitch} {roll}")

  # -- range checks and recovery -------------------------------------------------------------------

  def _get_soft_limits(self) -> Dict[PreciseFlexAxis, tuple]:
    """Every axis's soft limits - the arm's joints, the gripper and the rail; empty before setup."""
    gripper, rail = self._driver.gripper.configuration, self._driver.rail
    if self.configuration is None or gripper is None:
      return {}
    soft_limits = {
      **self.configuration.soft_limits,
      PreciseFlexAxis.GRIPPER: gripper.soft_limit_range,
    }
    if rail is not None and rail.configuration is not None:
      soft_limits[PreciseFlexAxis.RAIL] = rail.configuration.soft_limit_range
    return soft_limits

  # Axes recovered when out of range, safest first: the jaws, the Z column, the shoulder, the elbow.
  # Not the wrist, which can turn the gripper into the arm, nor the rail: setup raises on those.
  _RECOVERY_ORDER = (
    PreciseFlexAxis.GRIPPER,
    PreciseFlexAxis.BASE,
    PreciseFlexAxis.SHOULDER,
    PreciseFlexAxis.ELBOW,
  )

  def _axes_outside_soft_limits(self, joints: JointState) -> Dict[PreciseFlexAxis, tuple]:
    """Axes whose value lies outside their soft limit, as ``axis -> (value, (lo, hi))``.

    Iterates the soft-limit set (keyed by :class:`PreciseFlexAxis`) and looks each axis up in
    ``joints`` so the comparison stays PreciseFlexAxis-typed. Empty until the configuration has
    been discovered.
    """
    outside: Dict[PreciseFlexAxis, tuple] = {}
    for axis, (lo, hi) in self._get_soft_limits().items():
      value = joints.get(axis)
      if value is not None and not (lo <= value <= hi):
        outside[axis] = (value, (lo, hi))
    return outside

  @staticmethod
  def _fmt_axes(axes: Dict[PreciseFlexAxis, tuple]) -> str:
    """Format ``{axis: (value, (lo, hi))}`` for logs/errors, e.g.
    ``BASE at 0.959 (soft limit (1.5, 401.5))``."""
    return "; ".join(
      f"{axis.name} at {value} (soft limit {limit})" for axis, (value, limit) in axes.items()
    )

  async def recover_axes_within_limits(
    self,
    speed_percent: Optional[float] = None,
    max_distance: Optional[float] = 5.0,
  ) -> Dict[PreciseFlexAxis, float]:
    """Bring out-of-range axes back inside their soft limits, one axis at a time.

    While an axis is outside its soft limit the controller rejects every commanded
    coordinated move (-1012), and homing does not help on the absolute rotary axes.
    A single-axis move is the documented exception: it may move an axis toward the
    in-range region. Each recoverable offender is driven to just inside its nearest
    soft limit, slowly, waiting for each to finish, in :attr:`_RECOVERY_ORDER`.

    Args:
      speed_percent: Profile speed for the recovery moves, in percent; deliberately slow.
        ``default_recovery_speed_percent`` when None.
      max_distance: only move an axis that is out of range by at most this much (deg
        for the rotary axes, mm for base/gripper). An axis further out is left in place:
        a large unattended single-axis sweep risks a collision, so it is left for the
        caller to recover manually (e.g. by freedriving). Pass None to move regardless.

    Returns:
      The axes moved, as ``axis -> recovered target``. Empty when nothing recoverable
      is out of range or the configuration was not discovered. The wrist and rail are
      never auto-recovered (see :attr:`_RECOVERY_ORDER`).
    """
    if speed_percent is None:
      speed_percent = self.default_recovery_speed_percent
    joints = await self.request_joint_state()
    outside = self._axes_outside_soft_limits(joints)
    if not outside:
      return {}
    prior_speed = await self._request_speed()
    await self._set_speed(speed_percent)
    recovered: Dict[PreciseFlexAxis, float] = {}
    try:
      for axis in self._RECOVERY_ORDER:
        if axis not in outside:
          continue
        value, (lo, hi) = outside[axis]
        above = value > hi  # which limit is violated; both moves below hinge on this
        overshoot = (value - hi) if above else (lo - value)
        if max_distance is not None and overshoot > max_distance:
          continue  # too far out to move unattended; left for the post-condition to raise
        # Land just inside the violated limit. The 1-unit margin is at most half the range, so the
        # target stays inside a range narrower than the margin.
        margin = min(1.0, (hi - lo) / 2.0)
        target = (hi - margin) if above else (lo + margin)
        logger.warning(
          "[PreciseFlex %s] recovering %s from %s into soft limit [%s, %s] -> %s",
          self._driver.io._host,
          axis.name,
          value,
          lo,
          hi,
          target,
        )
        # Out of range is what is being recovered, so neither the limits nor -1012 are acted on.
        await self._guarded_move(
          lambda current: [{**current, axis: target}],
          lambda _targets: self._unchecked_fw_move_one_axis(axis, target),
          check_soft_limits=False,
          retry_out_of_range=False,
        )
        recovered[axis] = target
    finally:
      await self._set_speed(prior_speed)  # don't leave the profile at the slow recovery speed
    return recovered

  async def _handle_out_of_range_axes(self) -> None:
    """Warn about every out-of-range axis, then correct what is recoverable, or raise.

    While an axis is outside its soft limit the controller rejects every move with -1012. With
    ``recover_out_of_range`` on (the default) each recoverable axis is driven back; if recovery is
    off or leaves an axis out, this raises with the steps to recover by hand.

    Does nothing until the robot is homed: an unhomed incremental axis reads about 0, and the
    controller blocks the recovery move with -1021.
    """
    if not await self._driver._is_robot_homed():
      logger.warning(
        "[PreciseFlex %s] robot not homed; skipping the out-of-range check until it is "
        "(home() first - unhomed positions are unreliable and commanded moves are blocked).",
        self._driver.io._host,
      )
      return

    outside = self._axes_outside_soft_limits(await self.request_joint_state())
    if not outside:
      return
    logger.warning(
      "[PreciseFlex %s] axes out of soft limit at setup: %s",
      self._driver.io._host,
      self._fmt_axes(outside),
    )
    if self._driver._recover_out_of_range:
      await self.recover_axes_within_limits()
      outside = self._axes_outside_soft_limits(await self.request_joint_state())
    if outside:
      raise OutOfRangeOfMotionError(
        f"axis outside its soft limit after setup: {self._fmt_axes(outside)}. The controller "
        "rejects all commanded moves in this state. Recover with recover_axes_within_limits(), "
        "or freedrive the axis back into range manually (required for the wrist, or when an axis "
        "is far past its limit).",
        axes=outside,
      )

  def _assert_within_soft_limits(self, current: JointState, target: JointState) -> None:
    """Raise if the current pose or the target is outside the soft limits; -1012 on the controller.

    Does nothing until the configuration is discovered.

    Raises:
      OutOfRangeOfMotionError: If an axis stands out of range now. Homing does not fix it (the
        rotary axes are absolute); ``recover_axes_within_limits()`` does.
      ValueError: If the target is out of range, as a pose taught in freedrive can be; re-teach it.
    """
    out_of_range = self._axes_outside_soft_limits(current)
    if out_of_range:
      raise OutOfRangeOfMotionError(
        f"axis out of range: {self._fmt_axes(out_of_range)}. The controller rejects every "
        "commanded move while an axis is out of range. Homing will not recover it (the rotary "
        "axes are absolute); call recover_axes_within_limits() to drive it back into range.",
        axes=out_of_range,
      )
    for axis, (value, limit) in self._axes_outside_soft_limits(target).items():
      raise ValueError(
        f"{axis.name} target {value} is outside its soft limit {limit}; the controller "
        f"would reject the move (-1012). Re-teach this pose within the envelope."
      )

  def _forward_kinematics(self, joints: JointState) -> PreciseFlexPose:
    """Where a joint state puts every joint of the arm and its gripper. Nothing is read.

    Args:
      joints: the joint state, as ``request_joint_state`` returns it.
    """
    p = self._driver._kinematics_params
    elbow, wrist = kinematics.compute_elbow_and_wrist(
      p, math.radians(joints[PreciseFlexAxis.SHOULDER]), math.radians(joints[PreciseFlexAxis.ELBOW])
    )
    # The shoulder axis rides the rail and the Z drive, and the two joints stand from it.
    shoulder = Coordinate(
      x=joints.get(PreciseFlexAxis.RAIL, 0.0), y=0.0, z=joints[PreciseFlexAxis.BASE]
    )
    return PreciseFlexPose(
      shoulder_joint_location=shoulder,
      elbow_joint_location=shoulder + Coordinate(x=elbow[0], y=elbow[1], z=0.0),
      wrist_joint_location=shoulder + Coordinate(x=wrist[0], y=wrist[1], z=0.0),
      gripper_pose=kinematics.fk(joints, p),
      joints=dict(joints),
    )

  async def _guarded_move(
    self,
    build_targets: Callable[[JointState], Sequence[JointState]],
    unchecked_fw_move: Callable[[Sequence[JointState]], Awaitable[None]],
    *,
    check_soft_limits: bool = True,
    retry_out_of_range: bool = True,
  ) -> None:
    """The one path for every move whose target joint states are known before it is sent.

    Reads the live pose once the arm has stopped, builds the targets from it, checks each target
    against the soft limits, sends, and reads where the arm stopped, on success and failure alike.
    On -1012 with ``recover_out_of_range`` set it recovers the axes once and tries again.

    Args:
      build_targets: maps the pose just read to the joint states the move passes through, in
        order; run again for the second attempt.
      unchecked_fw_move: sends the move to those targets.
      check_soft_limits: whether each target is checked against the soft limits.
      retry_out_of_range: whether -1012 recovers the axes and tries once more.
    """

    async def attempt() -> None:
      current = await self.request_joint_state()
      targets = build_targets(current)
      if check_soft_limits:
        start = current
        for target in targets:
          self._assert_within_soft_limits(start, target)
          start = target
      try:
        await unchecked_fw_move(targets)
      finally:
        # A move returns once accepted: this waits for the arm to stop, then reads where it did.
        await self._request_joint_state_after_move()

    try:
      await attempt()
    except OutOfRangeOfMotionError as exc:
      if not (retry_out_of_range and self._driver._recover_out_of_range):
        raise
      host = self._driver.io._host
      logger.warning(
        "[PreciseFlex %s] commanded move blocked - %s; auto-recovery on -> recovering and retrying",
        host,
        self._fmt_axes(exc.axes),
      )
      await self.recover_axes_within_limits()
      try:
        await attempt()  # re-reads the live pose - recovery just moved an axis
      except OutOfRangeOfMotionError as exc2:
        logger.error(
          "[PreciseFlex %s] auto-recovery did not clear %s - freedrive/manual recovery needed",
          host,
          self._fmt_axes(exc2.axes),
        )
        raise
      logger.info("[PreciseFlex %s] out-of-range axes recovered; move retried successfully", host)

  # -- joint-space motion --------------------------------------------------------------------------

  @evented_operation(
    "precise_flex.move_to_joint_position",
    lambda self, joint_state, speed_percent=None: {
      "device": self._driver._controller_reference(),
      "target_joint_position": _joint_state_reference(joint_state),
      "speed_percent": speed_percent,
    },
  )
  async def move_to_joint_state(
    self,
    joint_state: JointState,
    speed_percent: Optional[float] = None,
  ) -> None:
    """Move the arm to the specified joint state. A partial spec is merged over the live pose;
    the move is guarded against out-of-range axes (see ``_guarded_move``).

    Args:
      joint_state: Target joint state. Omitted axes keep their live values.
      speed_percent: Movement speed override as a percentage (0-100). If None, uses the current
        speed setting. It stays set afterwards; use ``at_speed`` to scope a speed to one move.
    """
    if speed_percent is not None:
      await self._set_speed(speed_percent)
    await self._guarded_move(
      lambda current: [{**current, **joint_state}],
      lambda targets: self._unchecked_fw_move_j(self.profile_index, targets[0]),
    )

  async def request_gripper_pose(self) -> PreciseFlexCartesianPose:
    """Get the current pose using our kinematics model (no firmware `wherec`)."""
    _, pose = await self._request_state()
    return pose

  async def request_pose(self) -> PreciseFlexPose:
    """Where every joint of the arm is, worked out from the joint state; nothing else is asked."""
    return self._forward_kinematics(await self.request_joint_state())

  # -- cartesian motion ----------------------------------------------------------------------------

  def _require_rail(self) -> PreciseFlexRail:
    """The rail, on an arm that has one.

    Raises:
      RuntimeError: If the arm does not have a rail.
    """
    if self._driver.rail is None:
      raise RuntimeError("This arm does not have a rail.")
    return self._driver.rail

  @evented_operation(
    "precise_flex.move_to_location",
    lambda self, location, direction, speed_percent=None, orientation=None, wrist=None, rail_position=None: {
      "device": self._driver._controller_reference(),
      "target": _cartesian_target_reference(
        location,
        direction,
        orientation=orientation,
        wrist=wrist,
        rail_position=rail_position,
      ),
      "speed_percent": speed_percent,
    },
  )
  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    speed_percent: Optional[float] = None,
    orientation: Optional[ElbowOrientation] = None,
    wrist: Optional[Wrist] = None,
    rail_position: Optional[float] = None,
  ) -> None:
    """Move the arm to the specified Cartesian location. The IK target is guarded against
    out-of-range axes (see ``_guarded_move``).

    Args:
      location: Target Cartesian location.
      direction: Approach direction, applied as the pose's z rotation in degrees.
      speed_percent: Movement speed override as a percentage (0-100). If None, uses the current
        speed setting. It stays set afterwards; use ``at_speed`` to scope a speed to one move.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      wrist: Wrist configuration. If None, the robot picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
    """
    if speed_percent is not None:
      await self._set_speed(speed_percent)

    if rail_position is not None:
      await self._require_rail().move_rail(rail_position)
    elif self._driver._has_rail:
      raise ValueError(
        "Rail position must be specified for move_to_location when using a rail-equipped arm."
      )

    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(x=-180, y=90, z=direction),
      orientation=orientation,
      wrist=wrist,
    )
    await self._guarded_move(
      lambda current: [self._cart_to_joints(coords, current)],
      lambda targets: self._unchecked_fw_move_j(self.profile_index, targets[0]),
    )

  def _plan_cartesian_pose_route(
    self, poses: Sequence[PreciseFlexCartesianPose], current: JointState
  ) -> List[JointState]:
    """Plan a Cartesian pose route into joint targets, from the joint state already read.

    Unlike :meth:`_cart_to_joints`, this does not query the controller for every waypoint: it
    resolves each waypoint's IK from the previous waypoint's result. Omitted pose fields inherit
    from the previous pose so IK branch selection remains continuous across the route.
    """
    prev_joints, prev_pose = current, kinematics.fk(current, self._driver._kinematics_params)
    targets: List[JointState] = []
    for pose in poses:
      target, cart = self._plan_cartesian_pose(pose, prev_joints, prev_pose)
      targets.append(target)
      prev_joints = target
      prev_pose = cart
    return targets

  @evented_operation(
    "precise_flex.move_through_cartesian_poses",
    lambda self, poses, speed_percent=None, blend=True: {
      "device": self._driver._controller_reference(),
      "waypoint_count": len(poses),
      "start_target": (
        _cartesian_target_reference(
          poses[0].location,
          poses[0].rotation.z,
          orientation=poses[0].orientation,
          wrist=poses[0].wrist,
          rail_position=poses[0].rail_position,
        )
        if poses
        else None
      ),
      "end_target": (
        _cartesian_target_reference(
          poses[-1].location,
          poses[-1].rotation.z,
          orientation=poses[-1].orientation,
          wrist=poses[-1].wrist,
          rail_position=poses[-1].rail_position,
        )
        if poses
        else None
      ),
      "speed_percent": speed_percent,
      "blend": blend,
    },
  )
  async def move_through_cartesian_poses(
    self,
    poses: Sequence[PreciseFlexCartesianPose],
    speed_percent: Optional[float] = None,
    blend: bool = True,
  ) -> None:
    """Move through a sequence of Cartesian poses using one planned IK route.

    The standard Cartesian move path snapshots the current state for each waypoint,
    which waits for end-of-motion between moves. For taught air-transit routes, this
    method snapshots state once, plans each subsequent IK target from the previous
    planned target, queues the joint moves, and waits only after the final waypoint.

    This is a PreciseFlex-specific primitive: intermediate waypoints may be blended
    by the controller and should not be used for operations that require an exact
    stop, gripper action, or physical contact at every pose.

    Args:
      poses: Cartesian waypoints to move through, in order.
      speed_percent: Movement speed override as a percentage (0-100). If None, uses the
        current speed setting. It stays set afterwards; use ``at_speed`` to scope a speed.
      blend: When True, temporarily set the active motion profile's ``InRange`` value to
        ``-1`` so the controller may blend through intermediate waypoints instead of stopping
        at each one. The original profile is restored after the final waypoint is reached.
    """
    if not poses:
      return
    if speed_percent is not None:
      await self._set_speed(speed_percent)

    profile_index = self.profile_index
    blended: List[MotionProfile] = []

    async def unchecked_fw_move(targets: Sequence[JointState]) -> None:
      if blend:
        original_profile = await self.request_motion_profile_values(profile_index)
        if original_profile.in_range != BLEND_IN_RANGE:
          await self.set_motion_profile_values(*original_profile._replace(in_range=BLEND_IN_RANGE))
          blended.append(original_profile)
      for target in targets:
        await self._unchecked_fw_move_j(profile_index=profile_index, joint_coords=target)

    try:
      await self._guarded_move(
        lambda current: self._plan_cartesian_pose_route(poses, current), unchecked_fw_move
      )
    finally:
      # Restored once the read after the route has let queued motion settle: restoring InRange
      # mid-move would change the in-flight blend.
      for original_profile in blended:
        await self.set_motion_profile_values(*original_profile)

  async def _unchecked_fw_request_cartesian_destination(
    self, mode: int = 0
  ) -> tuple[float, float, float, float, float, float, int]:
    """Ask the controller for its own Cartesian destination or current location.

    The controller computes this answer itself. `request_gripper_pose` reads the joints and runs
    PLR's kinematics, and is what anything relying on a Cartesian position should call.

    Args:
      mode: Selects return value. Defaults to 0.
      0 = Return current Cartesian location if robot is not moving
      1 = Return target Cartesian location of the previous or current move

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll, config)
      If mode = 1 or robot is moving, returns the target location.
      If mode = 0 and robot is not moving, returns the current location.
    """
    if mode == 0:
      data = await self._driver.send_command("destC")
    else:
      data = await self._driver.send_command(f"destC {mode}")
    parts = data.split()
    if len(parts) != 7:
      raise PreciseFlexError(-1, "Unexpected response format from destC command.")
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts[:6])
    config = int(parts[6])
    return (x, y, z, yaw, pitch, roll, config)

  async def request_destination_joint_state(self, mode: int = 0) -> JointState:
    """Get the destination or current joint location of the robot.

    Args:
      mode: Selects return value. Defaults to 0.
      0 = Return current joint location if robot is not moving
      1 = Return target joint location of the previous or current move

    Returns:
      A dict mapping PreciseFlexAxis to float values.
      If mode = 1 or robot is moving, returns the target joint positions.
      If mode = 0 and robot is not moving, returns the current joint positions.
    """
    if mode == 0:
      data = await self._driver.send_command("destJ")
    else:
      data = await self._driver.send_command(f"destJ {mode}")
    parts = data.split()
    if not parts:
      raise PreciseFlexError(-1, "Unexpected response format from destJ command.")
    return self._parse_angles_response(parts)

  async def set_station_to_current_joint_state(self, station_index: int) -> None:
    """Record the current position of the selected robot into the specified Location as angles.

    The Location is automatically set to type "angles".

    Args:
      station_index: The station index, from 1 to N_LOC.
    """
    await self._driver.send_command(f"hereJ {station_index}")

  async def _unchecked_fw_set_station_to_current_cartesian_location(
    self, station_index: int
  ) -> None:
    """Record the current position of the selected robot into the specified Location as Cartesian.

    The Location object is automatically set to type "Cartesian".
    Can be used to change the pallet origin (index 1,1,1) value.

    Args:
      station_index: The station index, from 1 to N_LOC.
    """
    await self._driver.send_command(f"hereC {station_index}")

  # -- elbow orientation change --------------------------------------------------------------------

  async def _unchecked_fw_change_elbow_orientation(self, grip_mode: int) -> None:
    """Change righty to lefty or back through the customizable locations. Nothing is guarded."""
    await self._driver.send_command(f"ChangeConfig {grip_mode}")

  async def change_elbow_orientation(self, grip_mode: int = 0) -> None:
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
    await self._unchecked_fw_change_elbow_orientation(grip_mode)

  async def _unchecked_fw_change_elbow_orientation_by_algorithm(self, grip_mode: int) -> None:
    """Change righty to lefty or back by the controller's algorithm. Nothing is guarded."""
    await self._driver.send_command(f"ChangeConfig2 {grip_mode}")

  async def change_elbow_orientation_by_algorithm(self, grip_mode: int = 0) -> None:
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
    await self._unchecked_fw_change_elbow_orientation_by_algorithm(grip_mode)

  # -- pick up and drop ----------------------------------------------------------------------------

  async def _unchecked_fw_set_grip_detail(self):
    """Configure a default vertical station type for pick up and drop operations."""
    await self._driver.send_command(f"StationType {self.station_index} 1 0 100 0 10")

  async def _unchecked_fw_pick_plate(self) -> None:
    """Send ``pickplate`` for the station. Nothing is guarded.

    Raises:
      PreciseFlexError: If the controller answers that the gripper closed on nothing.
    """
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    ret_code = await self._driver.send_command(
      f"pickplate {self.station_index} {horizontal_compliance_int} "
      f"{self.horizontal_compliance_torque}"
    )
    if ret_code == "0":
      raise PreciseFlexError(-1, "the force-controlled gripper detected no plate present.")

  async def _unchecked_fw_place_plate(self) -> None:
    """Send ``placeplate`` for the station. Nothing is guarded."""
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    await self._driver.send_command(
      f"placeplate {self.station_index} {horizontal_compliance_int} "
      f"{self.horizontal_compliance_torque}"
    )

  async def _guarded_plate_j(
    self,
    build_station: Callable[[JointState], JointState],
    unchecked_fw_plate: Callable[[], Awaitable[None]],
  ) -> None:
    """Write the station and run the controller's pick or place at it, guarded.

    The station is the target. The controller's own approach, depart and any detour through its
    park pose are its own and not seen.

    Args:
      build_station: maps the pose just read to the station's full joint state.
      unchecked_fw_plate: `_unchecked_fw_pick_plate` or `_unchecked_fw_place_plate`.
    """

    async def unchecked_fw_move(targets: Sequence[JointState]) -> None:
      await self._unchecked_fw_set_joint_angles(self.station_index, targets[0])
      await self._unchecked_fw_set_grip_detail()
      await unchecked_fw_plate()

    await self._guarded_move(lambda current: [build_station(current)], unchecked_fw_move)

  @evented_operation(
    "precise_flex.pick_up_at_joint_position",
    lambda self, joint_state, resource_width, finger_speed_percent=None, grasp_force=None: {
      "device": self._driver._controller_reference(),
      "target_joint_position": _joint_state_reference(joint_state),
      "resource_width": float(resource_width),
      "finger_speed_percent": float(
        self._driver.gripper.default_finger_speed_percent
        if finger_speed_percent is None
        else finger_speed_percent
      ),
      "grasp_force": float(
        self._driver.gripper.default_grasp_force if grasp_force is None else grasp_force
      ),
    },
  )
  async def pick_up_at_joint_state(
    self,
    joint_state: JointState,
    resource_width: float,
    finger_speed_percent: Optional[float] = None,
    grasp_force: Optional[float] = None,
  ) -> None:
    """Pick up at the specified joint state.

    Args:
      joint_state: Joint state to pick from.
      resource_width: Width of the resource to grasp, in mm.
      finger_speed_percent: Finger closing speed as a percentage (0-100).
        ``default_finger_speed_percent`` when None.
      grasp_force: Grasp force in Newtons. ``default_grasp_force`` when None.
    """
    if finger_speed_percent is None:
      finger_speed_percent = self._driver.gripper.default_finger_speed_percent
    if grasp_force is None:
      grasp_force = self._driver.gripper.default_grasp_force
    logger.info(
      "[PreciseFlex %s] pick_up: joints=%s, resource_width_mm=%s",
      self._driver.io._host,
      joint_state,
      resource_width,
    )
    await self._driver.gripper._set_grasp_data(
      plate_width=resource_width,
      finger_speed_percent=finger_speed_percent,
      grasp_force=grasp_force,
    )
    await self._guarded_plate_j(
      lambda current: {**current, **joint_state}, self._unchecked_fw_pick_plate
    )

  @evented_operation(
    "precise_flex.drop_at_joint_position",
    lambda self, joint_state, resource_width: {
      "device": self._driver._controller_reference(),
      "target_joint_position": _joint_state_reference(joint_state),
      "resource_width": float(resource_width),
    },
  )
  async def drop_at_joint_state(
    self,
    joint_state: JointState,
    resource_width: float,
  ) -> None:
    """Drop at the specified joint state.

    Args:
      joint_state: Joint state to drop at.
      resource_width: Width of the held resource, in mm.
    """
    logger.info(
      "[PreciseFlex %s] drop: joints=%s, resource_width_mm=%s",
      self._driver.io._host,
      joint_state,
      resource_width,
    )
    await self._guarded_plate_j(
      lambda current: {**current, **joint_state}, self._unchecked_fw_place_plate
    )

  @evented_operation(
    "precise_flex.pick_up_at_location",
    lambda self, location, direction, resource_width, finger_speed_percent=None, grasp_force=None, orientation=None, wrist=None, rail_position=None: {
      "device": self._driver._controller_reference(),
      "target": _cartesian_target_reference(
        location,
        direction,
        orientation=orientation,
        wrist=wrist,
        rail_position=rail_position,
      ),
      "resource_width": float(resource_width),
      "finger_speed_percent": float(
        self._driver.gripper.default_finger_speed_percent
        if finger_speed_percent is None
        else finger_speed_percent
      ),
      "grasp_force": float(
        self._driver.gripper.default_grasp_force if grasp_force is None else grasp_force
      ),
    },
  )
  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    finger_speed_percent: Optional[float] = None,
    grasp_force: Optional[float] = None,
    orientation: Optional[ElbowOrientation] = None,
    wrist: Optional[Wrist] = None,
    rail_position: Optional[float] = None,
  ) -> None:
    """Pick up at the specified Cartesian location.

    Args:
      location: Cartesian location to pick from.
      direction: Approach direction, applied as the pose's z rotation in degrees.
      resource_width: Width of the resource to grasp, in mm.
      finger_speed_percent: Finger closing speed as a percentage (0-100).
        ``default_finger_speed_percent`` when None.
      grasp_force: Grasp force in Newtons. ``default_grasp_force`` when None.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      wrist: Wrist configuration. If None, the robot picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
    """
    if finger_speed_percent is None:
      finger_speed_percent = self._driver.gripper.default_finger_speed_percent
    if grasp_force is None:
      grasp_force = self._driver.gripper.default_grasp_force
    logger.info(
      "[PreciseFlex %s] pick_up: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self._driver.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if rail_position is not None:
      await self._require_rail().move_rail(rail_position)
    elif self._driver._has_rail:
      raise ValueError(
        "rail_position must be specified for pick_up_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(z=direction),
      orientation=orientation,
      wrist=wrist,
    )
    await self._driver.gripper._set_grasp_data(
      plate_width=resource_width,
      finger_speed_percent=finger_speed_percent,
      grasp_force=grasp_force,
    )
    await self._guarded_plate_j(
      lambda current: self._cart_to_joints(coords, current), self._unchecked_fw_pick_plate
    )

  @evented_operation(
    "precise_flex.drop_at_location",
    lambda self, location, direction, resource_width, orientation=None, wrist=None, rail_position=None: {
      "device": self._driver._controller_reference(),
      "target": _cartesian_target_reference(
        location,
        direction,
        orientation=orientation,
        wrist=wrist,
        rail_position=rail_position,
      ),
      "resource_width": float(resource_width),
    },
  )
  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    orientation: Optional[ElbowOrientation] = None,
    wrist: Optional[Wrist] = None,
    rail_position: Optional[float] = None,
  ) -> None:
    """Drop at the specified Cartesian location.

    Args:
      location: Cartesian location to drop at.
      direction: Approach direction, applied as the pose's z rotation in degrees.
      resource_width: Width of the held resource, in mm.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      wrist: Wrist configuration. If None, the robot picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
    """
    logger.info(
      "[PreciseFlex %s] drop: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self._driver.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if rail_position is not None:
      await self._require_rail().move_rail(rail_position)
    elif self._driver._has_rail:
      raise ValueError(
        "rail_position must be specified for drop_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(z=direction),
      orientation=orientation,
      wrist=wrist,
    )
    await self._guarded_plate_j(
      lambda current: self._cart_to_joints(coords, current), self._unchecked_fw_place_plate
    )

  # -- parking -------------------------------------------------------------------------------------

  def _validate_parking_position(self, position: JointState) -> None:
    """Reject anything that is not a JointState of in-range axes (limits checked once known)."""
    if not isinstance(position, dict) or not position:
      raise ValueError(f"parking_position must be a non-empty JointState, got {position!r}")
    for axis, value in position.items():
      if not isinstance(axis, PreciseFlexAxis):
        raise ValueError(f"parking_position keys must be PreciseFlexAxis members, got {axis!r}")
      if not isinstance(value, (int, float)):
        raise ValueError(f"parking_position[{axis.name}] must be a number, got {value!r}")
      soft_limits = self._get_soft_limits()
      if soft_limits:
        lo, hi = soft_limits[axis]
        if not lo <= value <= hi:
          raise ValueError(
            f"parking_position[{axis.name}]={value} is outside the soft limits [{lo}, {hi}]"
          )

  def _parking_pose_with_default_z(self, position: JointState) -> JointState:
    """Fill the Z column (``PreciseFlexAxis.BASE``) at 3/4 of the discovered travel when the pose omits it."""
    if PreciseFlexAxis.BASE in position or self.configuration is None:
      return position
    _, z_max = self.configuration.z_range
    return {PreciseFlexAxis.BASE: 0.75 * z_max, **position}

  @property
  def parking_position(self) -> Optional[JointState]:
    """The pose ``park()`` moves to. Assign one of the ``PARKING_POSITION_BACK/RIGHT/FRONT`` class
    constants or any JointState; the assignment is validated (keys must be ``PreciseFlexAxis`` members, values
    must be within the soft limits once the configuration is known). None until setup, where it
    defaults to ``PARKING_POSITION_RIGHT``. A pose that omits ``PreciseFlexAxis.BASE`` has its Z filled at park
    time."""
    return self._parking_position

  @parking_position.setter
  def parking_position(self, position: Optional[JointState]) -> None:
    if position is not None:
      self._validate_parking_position(position)
    self._parking_position: Optional[JointState] = dict(position) if position is not None else None

  async def _unchecked_fw_park(self) -> None:
    """Move to the controller's own safe position (``movetosafe``). Nothing is guarded."""
    await self._driver.send_command("movetosafe")

  @evented_operation(
    "precise_flex.park",
    lambda self: {"device": self._driver._controller_reference()},
  )
  async def park(self) -> None:
    """Move to ``self.parking_position``; defaults at setup, reassignable at runtime.

    ``parking_position`` is filled at setup with ``PARKING_POSITION_RIGHT`` (a planar fold facing
    right, Z column at 3/4 of its discovered travel); assign one of the ``PARKING_POSITION_*`` class
    constants or any JointState to park elsewhere. Falls back to the firmware ``movetosafe`` while
    it is unset. No collision checks against 3rd-party obstacles.
    """
    if self.parking_position is None:
      # Where the controller's own safe position is, the driver does not know: only read it after.
      try:
        await self._unchecked_fw_park()
      finally:
        await self._request_joint_state_after_move()
      return
    pose = self._parking_pose_with_default_z(self.parking_position)
    await self.move_to_joint_state(joint_state=pose)
