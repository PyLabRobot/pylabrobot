"""
KX2 kinematics: FK and IK.

The KX2 is *not* a dual-link SCARA. The elbow is a prismatic radial slide
(not a revolute J3), Z is a separate prismatic axis, and the rail is
outside this kinematic model. So the math is closed-form and trivially
cheap — no two-link cosine law, no elbow-up/elbow-down branch, no
unreachable-pose check beyond "rotation must be about +Z". The wrist is
a continuous-rotation drive with no winding, so J4 has no preferred sign
either — `ik` returns one canonical value, `snap_to_current` then pulls
it to whichever 360° wrap is closest to the current J4.

Joint dict keys match the drive node-IDs and the `KX2ArmBackend.Axis` enum:
  1: shoulder [deg]
  2: Z [mm]
  3: elbow [mm] (radial extension)
  4: wrist [deg]

Task pose is a `GripperLocation`. The gripper clamp point is in world
coordinates; rotation.z is yaw in degrees about world +Z, and
rotation.x/y must be 0. Sign convention follows right-hand rule about +Z
(CCW positive looking down).
"""

from math import asin, atan2, cos, degrees, hypot, pi, radians, sin, sqrt, trunc
from typing import Dict, Optional

from pylabrobot.capabilities.arms import kinematics as arm_kinematics
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.paa.kx2.config import Axis, GripperConfig, KX2Config
from pylabrobot.paa.kx2.driver import (
  JointMoveDirection,
  MotorMoveParam,
  MotorsMovePlan,
)
from pylabrobot.resources import Coordinate, Rotation


class IKError(ValueError):
  """Target pose is unreachable (for now: non-Z rotation requested)."""


def fk(joints: Dict[Axis, float], c: KX2Config, t: GripperConfig) -> GripperLocation:
  """Forward kinematics.

  Args:
    joints: {Axis.SHOULDER: deg, Axis.Z: mm, Axis.ELBOW: mm, Axis.WRIST: deg}.
    c: arm configuration (drive-read calibration).
    t: gripper tooling (user-supplied geometry).
  Returns:
    GripperLocation with the gripper clamp point and a yaw equivalent to
    the joints' net world orientation.
  """
  r = c.wrist_offset + c.elbow_offset + c.elbow_zero_offset + joints[Axis.ELBOW]
  sh_deg = joints[Axis.SHOULDER]
  sh = radians(sh_deg)

  wrist_x = -r * sin(sh)
  wrist_y = r * cos(sh)
  wrist_z = joints[Axis.Z]

  yaw_deg = joints[Axis.WRIST] + sh_deg
  if yaw_deg > 180.0:
    yaw_deg -= 360.0
  if yaw_deg < -180.0:
    yaw_deg += 360.0

  yaw = radians(yaw_deg)
  gl = t.length if t.finger_side == "barcode_reader" else -t.length
  return GripperLocation(
    location=Coordinate(
      x=wrist_x + gl * sin(yaw),
      y=wrist_y - gl * cos(yaw),
      z=wrist_z - t.z_offset,
    ),
    rotation=Rotation(z=yaw_deg),
  )


def ik(pose: GripperLocation, c: KX2Config, t: GripperConfig) -> Dict[Axis, float]:
  """Inverse kinematics.

  Args:
    pose: target gripper pose. rotation.x/y must be 0.
    c: arm configuration (drive-read calibration).
    t: gripper tooling (user-supplied geometry).
  Returns:
    joints dict {Axis.SHOULDER: deg, Axis.Z: mm, Axis.ELBOW: mm, Axis.WRIST: deg}.
    J4 is the canonical (-180°, 180°] solution; `snap_to_current` shifts
    it to the closest 360° wrap of the current J4 for actual motion.
  Raises:
    IKError if the requested rotation has an x or y component.
  """
  if pose.rotation.x != 0 or pose.rotation.y != 0:
    raise IKError("Only Z rotation is supported for KX2")

  # Gripper -> wrist: the incoming pose describes the gripper clamp point;
  # the joint-space math operates on the wrist axis. Rigid offset with the
  # gripper length on the radial axis (governed by world rotation z) and
  # the gripper z offset downward. Sign tracks which finger is the radial
  # "front".
  yaw = radians(pose.rotation.z)
  gl = t.length if t.finger_side == "barcode_reader" else -t.length
  x = pose.location.x - gl * sin(yaw)
  y = pose.location.y + gl * cos(yaw)
  wrist_z = pose.location.z + t.z_offset

  shoulder = -degrees(atan2(x, y))
  if abs(shoulder + 180.0) < c.eps:
    shoulder = 180.0

  elbow = hypot(x, y) - c.wrist_offset - c.elbow_offset - c.elbow_zero_offset
  wrist = pose.rotation.z - shoulder

  return {Axis.SHOULDER: shoulder, Axis.Z: wrist_z, Axis.ELBOW: elbow, Axis.WRIST: wrist}


def snap_to_current(
  joints: Dict[Axis, float], current: Dict[Axis, float]
) -> Dict[Axis, float]:
  """Shift rotary joints by 360° multiples toward `current`. Z and elbow
  are prismatic and pass through unchanged."""
  out = dict(joints)
  for axis in (Axis.SHOULDER, Axis.WRIST):
    out[axis] += 360 * round((current[axis] - out[axis]) / 360)
  return out


# --- elbow encoder/joint frame conversion ----------------------------------
#
# The elbow is exposed to FK/IK as a linear radial extension (mm), but the
# physical motor is a rotary actuator driving a sine linkage — so the encoder
# count is sin-related to the mm-space joint value. These two functions sit
# at the encoder/joint boundary; FK/IK above and the planner below both
# operate in the linear (mm) domain. Mirrors C# `ConvertElbowPositionToAngle`
# / `ConvertElbowAngleToPosition` (KX2RobotControl.cs:2944, 2974).

def convert_elbow_position_to_angle(cfg: KX2Config, elbow_pos: float) -> float:
  max_travel = cfg.axes[Axis.ELBOW].max_travel
  denom = max_travel + cfg.elbow_zero_offset
  if elbow_pos > max_travel:
    x = (2.0 * max_travel - elbow_pos + cfg.elbow_zero_offset) / denom
    return 90.0 + asin(x) * (180.0 / pi)
  x = (elbow_pos + cfg.elbow_zero_offset) / denom
  return asin(x) * (180.0 / pi)


def convert_elbow_angle_to_position(cfg: KX2Config, elbow_angle_deg: float) -> float:
  max_travel = cfg.axes[Axis.ELBOW].max_travel
  elbow_pos = (max_travel + cfg.elbow_zero_offset) * sin(elbow_angle_deg * (pi / 180.0)) - cfg.elbow_zero_offset
  if elbow_angle_deg > 90.0:
    elbow_pos = 2.0 * max_travel - elbow_pos
  return elbow_pos


# --- trajectory planning ---------------------------------------------------

def _wrap_to_range(x: float, lo: float, hi: float) -> float:
  span = hi - lo
  if span == 0:
    return lo
  k = trunc(x / span)
  x = x - k * span
  if x < lo:
    x += span
  if x == hi:
    x -= span
  return x


def _profile(dist: float, v: float, a: float) -> tuple:
  """Return (v, a, t_acc, t_total) with triangular fallback. If the
  distance is short, you can't reach v before you must decelerate."""
  if dist <= 0:
    return v, a, 0.0, 0.0
  if a <= 0:
    # degenerate; avoid crash
    return max(v, 1e-9), 1e-9, 0.0, dist / max(v, 1e-9)
  t_acc = v / a
  d_acc = 0.5 * a * t_acc * t_acc
  if 2.0 * d_acc > dist:  # triangular
    t_acc = sqrt(dist / a)
    v = a * t_acc
    return v, a, t_acc, 2.0 * t_acc
  d_cruise = dist - 2.0 * d_acc
  t_cruise = d_cruise / max(v, 1e-9)
  return v, a, t_acc, t_cruise + 2.0 * t_acc


def plan_joint_move(
  current: Dict[Axis, float],
  target: Dict[Axis, float],
  cfg: KX2Config,
  gripper_cfg: GripperConfig,
  *,
  max_gripper_speed: Optional[float] = None,
  max_gripper_acceleration: Optional[float] = None,
) -> Optional[MotorsMovePlan]:
  """Pure planner: joint-space target -> per-axis encoder plan.

  Caller owns the driver round-trip — pass ``current`` from
  ``request_joint_position`` (linear-extension units for elbow). Returns
  ``None`` if every axis would be a no-op (within 0.01 of current).

  ``max_gripper_speed`` / ``max_gripper_acceleration`` cap joint velocities
  so the worst-case Cartesian gripper speed/accel along the trajectory
  stays at or under the cap.
  """
  if max_gripper_speed is not None and max_gripper_speed <= 0.0:
    raise ValueError(f"max_gripper_speed must be positive, got {max_gripper_speed}")
  if max_gripper_acceleration is not None and max_gripper_acceleration <= 0.0:
    raise ValueError(f"max_gripper_acceleration must be positive, got {max_gripper_acceleration}")

  target = dict(target)
  axes = list(target.keys())

  # Travel-limit bounds check. Mirrors C# MoveAbsoluteSingleAxisPrivate
  # (KX2RobotControl.cs:4624-4649): snap if within 0.1 of the limit, raise
  # otherwise. Without this, an out-of-range target (e.g. gripper width 600
  # when max_travel ~30) parks the drive trying to reach an unreachable
  # position — MS never returns to 0 and every subsequent command on that
  # axis hangs until full re-setup. Run before the elbow position->angle
  # conversion so max/min_travel are compared in the units the user passed.
  for ax in axes:
    ax_cfg = cfg.axes[ax]
    if ax_cfg.unlimited_travel:
      continue
    t = target[ax]
    if t > ax_cfg.max_travel:
      if t - ax_cfg.max_travel < 0.1:
        target[ax] = ax_cfg.max_travel
      else:
        raise ValueError(f"Axis {ax.name} target {t} exceeds max_travel {ax_cfg.max_travel}")
    elif t < ax_cfg.min_travel:
      if ax_cfg.min_travel - t < 0.1:
        target[ax] = ax_cfg.min_travel
      else:
        raise ValueError(f"Axis {ax.name} target {t} below min_travel {ax_cfg.min_travel}")

  # Snapshot in cmd_pos units (elbow as linear extension) for the gripper-
  # speed cap helper, which iterates the path in `fk`'s natural units.
  target_cmd_units = dict(target)
  curr_cmd_units = dict(current)

  # Convert elbow target+current from position->angle for planning math —
  # the motor's vel/accel limits are encoder-rate, which is angle-rate, not
  # mm-rate. Time math is done in angle units; we convert back at the end.
  if Axis.ELBOW in axes:
    target[Axis.ELBOW] = convert_elbow_position_to_angle(cfg, target[Axis.ELBOW])
  curr = dict(current)
  if Axis.ELBOW in curr:
    curr[Axis.ELBOW] = convert_elbow_position_to_angle(cfg, curr[Axis.ELBOW])

  # Clearance check (in angle space for elbow, since base_to_gripper_clearance_arm
  # is defined in the same domain in C#).
  if Axis.Z in axes:
    if (
      target[Axis.Z] < cfg.axes[Axis.Z].min_travel + cfg.base_to_gripper_clearance_z
      and target[Axis.ELBOW] < cfg.base_to_gripper_clearance_arm
    ):
      raise ValueError("Base-to-gripper clearance violated")

  # Unlimited-travel normalization for non-NORMAL direction modes.
  for ax in axes:
    ax_cfg = cfg.axes[ax]
    if ax_cfg.unlimited_travel and ax_cfg.joint_move_direction != JointMoveDirection.Normal:
      target[ax] = _wrap_to_range(target[ax], ax_cfg.min_travel, ax_cfg.max_travel)

  # Direction-aware deltas in cmd_pos units (elbow as linear extension), for
  # the gripper-speed cap helper. Identical logic to the dist computation
  # below, but evaluated against the un-converted joints so the cap helper
  # sees the trajectory the arm actually walks.
  cap_deltas: Dict[Axis, float] = {}
  for ax in axes:
    ax_cfg = cfg.axes[ax]
    if ax_cfg.unlimited_travel:
      d = target_cmd_units[ax] - curr_cmd_units.get(ax, 0.0)
      span = ax_cfg.max_travel - ax_cfg.min_travel
      dir_ = ax_cfg.joint_move_direction
      if dir_ == JointMoveDirection.Clockwise and d > 0.01:
        d -= span
      elif dir_ == JointMoveDirection.Counterclockwise and d < -0.01:
        d += span
      elif dir_ == JointMoveDirection.ShortestWay:
        if d > 180.0:
          d -= span
        elif d < -180.0:
          d += span
      cap_deltas[ax] = d
    else:
      cap_deltas[ax] = target_cmd_units[ax] - curr_cmd_units.get(ax, 0.0)

  # Per-axis caps from the gripper-speed limit. Helper iterates the joint
  # path in `fk`'s natural units. Servo gripper isn't in fk, so it always
  # runs at firmware max regardless of cap.
  arm_axes = (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)
  fk_start = {ax: curr_cmd_units[ax] for ax in arm_axes if ax in curr_cmd_units}
  fk_deltas = {ax: cap_deltas.get(ax, 0.0) for ax in arm_axes if ax in fk_start}
  capped_v: Dict[Axis, float] = {}
  capped_a: Dict[Axis, float] = {}
  fk_loc = lambda j: fk(j, cfg, gripper_cfg).location
  if max_gripper_speed is not None and fk_start:
    result = arm_kinematics.joint_velocities_for_max_gripper_speed(
      fk=fk_loc,
      joints_start=fk_start,
      joint_deltas=fk_deltas,
      joint_max_velocities={ax: cfg.axes[ax].max_vel for ax in fk_start},
      max_gripper_speed=max_gripper_speed,
      num_samples=1000,
      eps=1e-3,
    )
    capped_v = {ax: abs(vv) for ax, vv in result.items()}
  if max_gripper_acceleration is not None and fk_start:
    result = arm_kinematics.joint_velocities_for_max_gripper_speed(
      fk=fk_loc,
      joints_start=fk_start,
      joint_deltas=fk_deltas,
      joint_max_velocities={ax: cfg.axes[ax].max_accel for ax in fk_start},
      max_gripper_speed=max_gripper_acceleration,
      num_samples=1000,
      eps=1e-3,
    )
    capped_a = {ax: abs(aa) for ax, aa in result.items()}

  # Distances + initial v/a per axis (planning units = angle for elbow).
  dist: Dict[Axis, float] = {}
  v: Dict[Axis, float] = {}
  a: Dict[Axis, float] = {}
  accel_time: Dict[Axis, float] = {}
  total_time: Dict[Axis, float] = {}
  skip_ax: Dict[Axis, bool] = {}
  for ax in axes:
    ax_cfg = cfg.axes[ax]
    if ax_cfg.unlimited_travel:
      d = target[ax] - curr[ax]
      span = ax_cfg.max_travel - ax_cfg.min_travel
      dir_ = ax_cfg.joint_move_direction
      if dir_ == JointMoveDirection.Clockwise and d > 0.01:
        d -= span
      elif dir_ == JointMoveDirection.Counterclockwise and d < -0.01:
        d += span
      elif dir_ == JointMoveDirection.ShortestWay:
        if d > 180.0:
          d -= span
        elif d < -180.0:
          d += span
      dist[ax] = abs(d)
    else:
      dist[ax] = abs(target[ax] - curr[ax])

    skip_ax[ax] = abs(dist[ax]) < 0.01
    v[ax] = capped_v.get(ax, ax_cfg.max_vel)
    a[ax] = capped_a.get(ax, ax_cfg.max_accel)
    if not skip_ax[ax] and a[ax] > 0:
      v[ax], a[ax], accel_time[ax], total_time[ax] = _profile(dist[ax], v[ax], a[ax])
    else:
      total_time[ax] = 0.0
      accel_time[ax] = 0.0

  if all(skip_ax[ax] for ax in axes):
    return None

  # Sync accel times to the lead axis so all axes ramp together.
  lead_acc_ax = max((ax for ax in axes if not skip_ax[ax]), key=lambda ax: accel_time[ax])
  lead_acc_t = accel_time[lead_acc_ax]
  for ax in axes:
    if ax == lead_acc_ax or skip_ax[ax]:
      continue
    if accel_time[ax] > lead_acc_t:
      v[ax] = lead_acc_t * a[ax]
    elif accel_time[ax] < lead_acc_t:
      a[ax] = v[ax] / max(lead_acc_t, 1e-9)

  for ax in axes:
    if skip_ax[ax]:
      total_time[ax] = 0.0
      continue
    v[ax], a[ax], _, total_time[ax] = _profile(dist[ax], v[ax], a[ax])

  # Sync total times to the lead axis.
  lead_time_ax = max(axes, key=lambda ax: total_time[ax])
  lead_T = total_time[lead_time_ax]
  for ax in axes:
    if ax == lead_time_ax or skip_ax[ax]:
      continue
    denom = v[ax] * (lead_T - (v[ax] / max(a[ax], 1e-9)))
    if abs(denom) < 1e-12:
      continue
    k = dist[ax] / denom
    v[ax] *= k
    a[ax] *= k

  for ax in axes:
    if skip_ax[ax]:
      total_time[ax] = 0.0
      continue
    v[ax], a[ax], _, total_time[ax] = _profile(dist[ax], v[ax], a[ax])

  move_time = max(total_time[ax] for ax in axes)

  # Convert back to encoder units. Elbow target is still in angle space here
  # — the encoder count for an elbow joint is angle * conv, not mm * conv.
  moves = []
  for ax in axes:
    ax_cfg = cfg.axes[ax]
    conv = ax_cfg.motor_conversion_factor
    enc_pos = target[ax] * conv
    if skip_ax[ax]:
      enc_vel = 1000.0
      enc_accel = 1000.0
    else:
      enc_vel = max(v[ax] * abs(conv), 1.0)
      enc_accel = max(a[ax] * abs(conv), 1.0)
    moves.append(MotorMoveParam(
      node_id=int(ax),
      position=int(round(enc_pos)),
      velocity=int(round(enc_vel)),
      acceleration=int(round(enc_accel)),
      direction=ax_cfg.joint_move_direction,
    ))
  return MotorsMovePlan(moves=moves, move_time=move_time)
