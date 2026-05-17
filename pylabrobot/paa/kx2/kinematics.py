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

Task pose is a `CartesianPose`. ``location`` is the gripper's *grip
center* (the geometric midpoint between the two jaws, where a held
plate sits) in world coordinates; rotation.z is yaw in degrees about
world +Z, and rotation.x/y must be 0. Sign convention follows right-hand
rule about +Z (CCW positive looking down).

`rotation.z` is the world direction in which the *front* finger
of the gripper points — where "front" is whichever finger
:attr:`GripperParams.finger_side` names. The gripper assembly hangs
``t.length`` away from the wrist axis along its extension direction;
the *grip center* is at that offset, and the two fingers cluster
around it. Flipping `finger_side` is just a 180° relabel of which
finger is the "front", so for the same joint state the grip center
stays put and only the reported yaw flips by 180°. For the same
(grip center, yaw) target, the wrist *axis* lands on opposite sides
of the grip center for the two side choices (separated by
``2·t.length`` along the front-finger axis), because the gripper
assembly has to swing around the wrist motor to point the chosen
finger forward.
"""

from dataclasses import dataclass, field
from math import asin, atan2, ceil, cos, degrees, hypot, pi, radians, sin, sqrt, trunc
from typing import Callable, Dict, List, Optional, Tuple

from pylabrobot.capabilities.arms import kinematics as arm_kinematics
from pylabrobot.capabilities.arms.standard import CartesianPose
from pylabrobot.paa.kx2.config import Axis, GripperParams, KX2Config
from pylabrobot.paa.kx2.driver import (
  JointMoveDirection,
  MotorMoveParam,
  MotorsMovePlan,
)
from pylabrobot.resources import Coordinate, Rotation


class IKError(ValueError):
  """Target pose is unreachable (for now: non-Z rotation requested)."""


# Floating-point fudge for boundary checks (e.g. snap shoulder to ±180°).
_EPS = 1e-6


def fk(joints: Dict[Axis, float], c: KX2Config, t: GripperParams) -> CartesianPose:
  """Forward kinematics.

  Args:
    joints: {Axis.SHOULDER: deg, Axis.Z: mm, Axis.ELBOW: mm, Axis.WRIST: deg}.
    c: arm configuration (drive-read calibration).
    t: gripper tooling (user-supplied geometry).
  Returns:
    CartesianPose where ``location`` is the gripper's *grip center*
    (geometric midpoint of the jaws) and ``rotation.z`` is the world
    yaw of the *front* finger (the one named by ``t.finger_side``).
    Yaw is wrapped to ``[-180, 180]``. The grip center depends only
    on the joint state — flipping ``t.finger_side`` for fixed joints
    leaves it unchanged and shifts only the reported yaw by 180°.
  """
  r = c.wrist_offset + c.elbow_offset + c.elbow_zero_offset + joints[Axis.ELBOW]
  sh_deg = joints[Axis.SHOULDER]
  sh = radians(sh_deg)

  wrist_x = -r * sin(sh)
  wrist_y = r * cos(sh)
  wrist_z = joints[Axis.Z]

  # Gripper assembly hangs t.length off the wrist axis along the
  # extension direction (= world angle of the wrist motor = WRIST+SHOULDER).
  # finger_side just relabels which physical finger is "front", so it
  # only shifts the reported yaw by 180°; the grip center is fixed.
  ext_deg = joints[Axis.WRIST] + sh_deg
  ext = radians(ext_deg)

  yaw_deg = ext_deg
  if t.finger_side == "proximity_sensor":
    yaw_deg += 180.0
  while yaw_deg > 180.0:
    yaw_deg -= 360.0
  while yaw_deg < -180.0:
    yaw_deg += 360.0

  return CartesianPose(
    location=Coordinate(
      x=wrist_x + t.length * sin(ext),
      y=wrist_y - t.length * cos(ext),
      z=wrist_z - t.z_offset,
    ),
    rotation=Rotation(z=yaw_deg),
  )


def ik(pose: CartesianPose, c: KX2Config, t: GripperParams) -> Dict[Axis, float]:
  """Inverse kinematics.

  Args:
    pose: target gripper pose. ``location`` is the *grip center*
      (geometric midpoint of the jaws). ``rotation.z`` is the world
      direction the *front* finger should face (per ``t.finger_side``).
      ``rotation.x/y`` must be 0.
    c: arm configuration (drive-read calibration).
    t: gripper tooling (user-supplied geometry).
  Returns:
    joints dict {Axis.SHOULDER: deg, Axis.Z: mm, Axis.ELBOW: mm, Axis.WRIST: deg}.
    Shoulder and elbow differ between the two finger-side choices for
    the same target — the gripper assembly swings around the wrist
    motor, so the wrist axis lands on opposite sides of the grip
    center (separated by ``2·t.length`` along the front-finger axis).
    J4 is the canonical (-180°, 180°] solution; `snap_to_current`
    then pulls it to whichever 360° wrap is closest to the current J4.
  Raises:
    IKError if the requested rotation has an x or y component.
  """
  if pose.rotation.x != 0 or pose.rotation.y != 0:
    raise IKError("Only Z rotation is supported for KX2")

  # The incoming pose describes the grip center; the joint-space math
  # operates on the wrist axis. The gripper assembly hangs ``t.length``
  # off the wrist axis along its *extension direction*, so the grip
  # center sits at wrist + t.length·<extension>. For barcode_reader
  # the extension direction == the front-finger direction; for
  # proximity_sensor the front finger has been swung 180° to the other
  # side, so the extension (and therefore grip-center offset) points
  # 180° opposite the front finger.
  ext_deg = pose.rotation.z
  if t.finger_side == "proximity_sensor":
    ext_deg -= 180.0
  ext = radians(ext_deg)
  x = pose.location.x - t.length * sin(ext)
  y = pose.location.y + t.length * cos(ext)
  wrist_z = pose.location.z + t.z_offset

  # atan2 returns (-π, π]; on the -Y axis it yields -180°. Snap to +180°
  # so the boundary lands on the "in-range" side of an exclusive max
  # convention and downstream sign comparisons are stable.
  shoulder = -degrees(atan2(x, y))
  if abs(shoulder + 180.0) < _EPS:
    shoulder = 180.0

  elbow = hypot(x, y) - c.wrist_offset - c.elbow_offset - c.elbow_zero_offset
  # Wrist motor's world angle == extension direction. Joint space:
  # wrist_joint = ext_world - shoulder.
  wrist = ext_deg - shoulder

  joints = {Axis.SHOULDER: shoulder, Axis.Z: wrist_z, Axis.ELBOW: elbow, Axis.WRIST: wrist}

  # Enforce per-axis joint travel limits. Rotary axes (shoulder, wrist) are
  # 360°-periodic and a downstream `snap_to_current` may wrap them — check
  # only the *non-wrappable* representative (the canonical solution in this
  # call). Linear axes (Z, elbow) get a strict check. Raising here aborts
  # the caller before any motion command leaves the host.
  for ax in (Axis.Z, Axis.ELBOW):
    cfg_ax = c.axes[ax]
    val = joints[ax]
    if val < cfg_ax.min_travel or val > cfg_ax.max_travel:
      raise IKError(
        f"{ax.name} out of range: {val:.3f} not in "
        f"[{cfg_ax.min_travel:.3f}, {cfg_ax.max_travel:.3f}] "
        f"for pose location={pose.location}, yaw={pose.rotation.z}"
      )
  for ax in (Axis.SHOULDER, Axis.WRIST):
    cfg_ax = c.axes[ax]
    if cfg_ax.unlimited_travel:
      continue
    # Pull the canonical solution into the range straddling the limits before
    # checking, so a -180° solution against a [-90, 270] axis doesn't false-fail.
    val = joints[ax]
    mid = 0.5 * (cfg_ax.min_travel + cfg_ax.max_travel)
    val += 360.0 * round((mid - val) / 360.0)
    if val < cfg_ax.min_travel or val > cfg_ax.max_travel:
      raise IKError(
        f"{ax.name} out of range: {val:.3f}° not in "
        f"[{cfg_ax.min_travel:.3f}, {cfg_ax.max_travel:.3f}]° "
        f"for pose location={pose.location}, yaw={pose.rotation.z}"
      )

  return joints


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
  # Clamp to [-1, 1]: floating-point overshoot at the joint limit (e.g.
  # encoder reads max_travel + 1e-12) would otherwise raise ValueError
  # from asin even though the position is physically reachable.
  if elbow_pos > max_travel:
    x = max(-1.0, min(1.0, (2.0 * max_travel - elbow_pos + cfg.elbow_zero_offset) / denom))
    return 90.0 + asin(x) * (180.0 / pi)
  x = max(-1.0, min(1.0, (elbow_pos + cfg.elbow_zero_offset) / denom))
  return asin(x) * (180.0 / pi)


def convert_elbow_angle_to_position(cfg: KX2Config, elbow_angle_deg: float) -> float:
  max_travel = cfg.axes[Axis.ELBOW].max_travel
  denom = max_travel + cfg.elbow_zero_offset
  if elbow_angle_deg > 90.0:
    # Inverse of `90 + asin((2·max − pos + zero)/(max + zero))`. The `+ zero`
    # term has to appear on both sides of the reflection or the round-trip
    # drifts by ~zero·(max+zero)/max — silent encoder-read miscalibration
    # when the joint is past peak extension.
    return 2.0 * max_travel + cfg.elbow_zero_offset - denom * sin((elbow_angle_deg - 90.0) * (pi / 180.0))
  return denom * sin(elbow_angle_deg * (pi / 180.0)) - cfg.elbow_zero_offset


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


def _directional_delta(target: float, current: float, ax_cfg) -> float:
  """Signed joint delta honoring `unlimited_travel` direction modes.

  For non-`unlimited_travel` axes the delta is the literal `target − current`.
  For unlimited axes the delta is rewritten to walk the direction the config
  asks for (CW=negative, CCW=positive, ShortestWay=≤180°)."""
  d = target - current
  if not ax_cfg.unlimited_travel:
    return d
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
  return d


def _stretch_to_time(dist: float, v: float, a: float, T: float) -> tuple:
  """Slow a single-axis trapezoidal/triangular profile so it lands at time T.

  Given the pass-1 profile (v, a) for `dist`, scale (v, a) by the same factor
  k so the new profile takes time T. Preserving the v/a ratio keeps the
  acceleration-time fixed; only the cruise leg stretches. Returns (v, a) for
  the stretched profile."""
  if dist <= 0 or a <= 0 or v <= 0:
    return v, a
  denom = v * (T - v / a)
  if abs(denom) < 1e-12:
    return v, a
  k = dist / denom
  return v * k, a * k


def plan_joint_move(
  current: Dict[Axis, float],
  target: Dict[Axis, float],
  cfg: KX2Config,
  gripper_params: GripperParams,
  *,
  max_gripper_speed: Optional[float] = None,
  max_gripper_acceleration: Optional[float] = None,
) -> Optional[MotorsMovePlan]:
  """Pure planner: joint-space target -> per-axis encoder plan.

  Caller owns the driver round-trip — pass ``current`` from
  ``request_joint_position`` (linear-extension units for elbow). Returns
  ``None`` if every axis would be a no-op (within 0.01 of current).

  ``target`` may be a subset of axes (e.g. ``{Axis.Z: 100}``) — unspecified
  axes don't move.

  ``current`` must include all four arm axes (SHOULDER/Z/ELBOW/WRIST) when
  a gripper-speed/accel cap is given: the cap helper evaluates the FK
  Jacobian at the start pose, and the column for any moving axis depends
  on the absolute position of every other arm axis (the radius from the
  shoulder enters the shoulder Jacobian, etc.). The orchestrator always
  passes a full ``current`` from ``request_joint_position``; tests calling
  this function directly must do the same.

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
  # the gripper-speed cap helper that walks the path in `fk`'s natural units.
  cap_deltas: Dict[Axis, float] = {
    ax: _directional_delta(target_cmd_units[ax], curr_cmd_units.get(ax, 0.0), cfg.axes[ax])
    for ax in axes
  }

  # Per-axis caps from the gripper-speed/accel limits. Servo gripper isn't in
  # fk, so it always runs at firmware max regardless of cap.
  arm_axes = (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)
  cap_requested = max_gripper_speed is not None or max_gripper_acceleration is not None
  cap_relevant = any(ax in arm_axes for ax in axes)
  if cap_requested and cap_relevant:
    missing = [ax for ax in arm_axes if ax not in curr_cmd_units]
    if missing:
      raise ValueError(
        f"max_gripper_speed/acceleration requires `current` to include all "
        f"four arm axes (SHOULDER/Z/ELBOW/WRIST); missing: "
        f"{[ax.name for ax in missing]}"
      )
  fk_start = {ax: curr_cmd_units[ax] for ax in arm_axes if ax in curr_cmd_units}
  fk_deltas = {ax: cap_deltas.get(ax, 0.0) for ax in arm_axes if ax in fk_start}
  fk_loc = lambda j: fk(j, cfg, gripper_params).location
  capped: Dict[str, Dict[Axis, float]] = {"v": {}, "a": {}}
  for kind, cap, field in (
    ("v", max_gripper_speed, "max_vel"),
    ("a", max_gripper_acceleration, "max_accel"),
  ):
    if cap is None or not fk_start:
      continue
    result = arm_kinematics.joint_velocities_for_max_gripper_speed(
      fk=fk_loc,
      joints_start=fk_start,
      joint_deltas=fk_deltas,
      joint_max_velocities={ax: getattr(cfg.axes[ax], field) for ax in fk_start},
      max_gripper_speed=cap,
      num_samples=1000,
      eps=1e-3,
    )
    capped[kind] = {ax: abs(x) for ax, x in result.items()}
  capped_v, capped_a = capped["v"], capped["a"]

  # Per-axis trajectory (planning units = angle for elbow). Three-pass sync:
  # (1) profile each axis at its firmware max; (2) match accel-times to the
  # slowest-accel axis (shrink `a` so all ramps end together); (3) scale (v,a)
  # together so all axes finish together at the slowest total time.
  dist: Dict[Axis, float] = {}
  v: Dict[Axis, float] = {}
  a: Dict[Axis, float] = {}
  ta: Dict[Axis, float] = {}
  tt: Dict[Axis, float] = {}
  for ax in axes:
    ax_cfg = cfg.axes[ax]
    dist[ax] = abs(_directional_delta(target[ax], curr[ax], ax_cfg))
    v[ax] = capped_v.get(ax, ax_cfg.max_vel)
    a[ax] = capped_a.get(ax, ax_cfg.max_accel)
    if dist[ax] >= 0.01 and a[ax] > 0:
      v[ax], a[ax], ta[ax], tt[ax] = _profile(dist[ax], v[ax], a[ax])
    else:
      ta[ax] = tt[ax] = 0.0
  moving = [ax for ax in axes if tt[ax] > 0.0]
  if not moving:
    return None

  lead_acc_t = max(ta[ax] for ax in moving)
  for ax in moving:
    if ta[ax] < lead_acc_t:
      a[ax] = v[ax] / lead_acc_t
    v[ax], a[ax], _, tt[ax] = _profile(dist[ax], v[ax], a[ax])

  lead_T = max(tt[ax] for ax in moving)
  for ax in moving:
    if tt[ax] < lead_T:
      v[ax], a[ax] = _stretch_to_time(dist[ax], v[ax], a[ax], lead_T)
      v[ax], a[ax], _, tt[ax] = _profile(dist[ax], v[ax], a[ax])

  move_time = max(tt[ax] for ax in moving)

  # Convert back to encoder units. Elbow target is still in angle space here
  # — the encoder count for an elbow joint is angle * conv, not mm * conv.
  moves = []
  for ax in axes:
    ax_cfg = cfg.axes[ax]
    conv = ax_cfg.motor_conversion_factor
    enc_pos = target[ax] * conv
    # Skipped axes get firmware max — same formula as moving axes. They
    # don't move (dist < 0.01), so vel/accel are nominal; the previous
    # 1000.0 constant was 0.03–4% of firmware max across axes, leaving
    # the drive's profile registers in a pathologically slow state if a
    # follow-up step ever picked them up.
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


# --- Cartesian-linear path sampling (for IPM/PVT streaming) ----------------
#
# `plan_joint_move` generates a *joint*-space trapezoid: every axis ramps in
# parallel through its own (v, a) profile, which gives a curvy tool-tip path.
# `sample_linear_path` instead generates a *Cartesian*-linear path: the
# gripper travels the straight line from start to end, sampled at fixed dt,
# and IK at each sample yields the joint-space trajectory. The KX2ArmBackend
# streams the result into the drive's interpolation buffer (PVT mode) when
# `CartesianMoveParams(path='linear')` is requested.

# Arm axes that show up in FK/IK and therefore have an entry in every
# `LinearPathSample.joints`. Servo gripper and rail are not Cartesian-driven.
_LINEAR_PATH_AXES: Tuple[Axis, ...] = (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)


@dataclass
class LinearPathSample:
  """One frame of a Cartesian-linear trajectory.

  ``joints`` is in planning units (mm for Z, deg for SHOULDER/WRIST,
  *linear extension mm* for ELBOW). ``encoder_position`` /
  ``encoder_velocity`` are post-conversion: ELBOW is converted through
  ``convert_elbow_position_to_angle`` first because the encoder counts are
  driven by the rotary actuator, not the linear projection. The runtime
  feeds these straight into ``ipm_send_pvt_point``.
  """

  time_s: float
  joints: Dict[Axis, float] = field(default_factory=dict)
  encoder_position: Dict[Axis, int] = field(default_factory=dict)
  encoder_velocity: Dict[Axis, int] = field(default_factory=dict)


def _arc_length_trapezoid_times(
  length: float, vel: float, accel: float, dt_s: float,
) -> List[float]:
  """Return arc-length s(t_i) at evenly-spaced t_i = i*dt covering a trapezoidal
  profile from 0 to ``length`` with peak speed ``vel`` and peak accel ``accel``.

  Falls back to triangular when the path is too short to reach ``vel``.

  The trajectory is **dt-aligned**: the last sample lands at exactly
  ``ceil(t_total / dt) * dt`` with s = length, and one extra "hold" sample
  at length is appended so the central-difference velocity at the final
  point is exactly zero. This matters for IPM streaming — the drive
  integrates a cubic Hermite spline through (P_i, V_i) → (P_{i+1}, V_{i+1})
  over each dt; without the hold, the FD-derived V at the last sample
  would be small-but-nonzero, and the cubic would overshoot the target
  before snapping back.
  """
  if length <= 0 or dt_s <= 0:
    return [0.0]
  v, a, t_acc, t_total = _profile(length, vel, accel)
  if t_total <= 0:
    return [0.0]
  d_acc = 0.5 * a * t_acc * t_acc
  n_motion = max(2, ceil(t_total / dt_s) + 1)
  out: List[float] = []
  for i in range(n_motion):
    t = i * dt_s
    if t >= t_total:
      out.append(length)
    elif t < t_acc:
      out.append(0.5 * a * t * t)
    elif t < t_total - t_acc:
      out.append(d_acc + v * (t - t_acc))
    else:
      td = t_total - t
      out.append(length - 0.5 * a * td * td)
  out.append(length)  # trailing hold so FD-derived final V is exactly 0
  return out


def _yaw_lerp(yaw_a_deg: float, yaw_b_deg: float, alpha: float) -> float:
  """Interpolate yaw the short way around the circle. Avoids a 359° unwind
  when start=+179°, end=-179° (true delta = 2°, naive lerp delta = 358°)."""
  delta = (yaw_b_deg - yaw_a_deg + 540.0) % 360.0 - 180.0
  return yaw_a_deg + alpha * delta


def sample_linear_path(
  cfg: KX2Config,
  gripper_params: GripperParams,
  start_pose: CartesianPose,
  end_pose: CartesianPose,
  *,
  vel_mm_per_s: float,
  accel_mm_per_s2: float,
  dt_s: float,
  current_joints: Optional[Dict[Axis, float]] = None,
) -> List[LinearPathSample]:
  """Sample a straight tool-tip path from ``start_pose`` to ``end_pose`` at dt.

  Args:
    cfg: drive-read calibration.
    gripper_params: tooling geometry.
    start_pose, end_pose: Cartesian endpoints (gripper clamp point + yaw).
    vel_mm_per_s: peak Cartesian speed along the path.
    accel_mm_per_s2: peak Cartesian acceleration along the path.
    dt_s: sample period (matches the dt fed to ``ipm_set_time_interval``).
    current_joints: pre-move joint snapshot. If supplied, the first sample's
      WRIST/SHOULDER are 360°-snapped toward ``current_joints`` so the
      trajectory doesn't begin with a full unwind. ELBOW position passes
      through unchanged (linear axis).

  Returns:
    Dense list of samples, one per dt step. The last sample lands exactly
    on ``end_pose``. ``encoder_position`` / ``encoder_velocity`` are ready
    to feed straight into ``ipm_send_pvt_point``. Velocity is the central
    finite difference of encoder positions; the endpoints use one-sided
    differences and are clamped to zero at the very last sample so the
    drive ends stationary.
  """
  if vel_mm_per_s <= 0 or accel_mm_per_s2 <= 0 or dt_s <= 0:
    raise ValueError(
      f"sample_linear_path: vel/accel/dt must be positive (got "
      f"{vel_mm_per_s}, {accel_mm_per_s2}, {dt_s})"
    )

  sx, sy, sz = start_pose.location.x, start_pose.location.y, start_pose.location.z
  ex, ey, ez = end_pose.location.x, end_pose.location.y, end_pose.location.z
  start_yaw, end_yaw = start_pose.rotation.z, end_pose.rotation.z
  length = sqrt((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2)

  # Pure rotation in place is unsupported here: the speed/accel caps are
  # mm/s and mm/s², so reusing them as deg/s for a wrist spin would silently
  # command rotational rates the caller didn't intend. Use a joint-space
  # move for orientation-only changes.
  rot_delta = abs(((end_yaw - start_yaw + 540.0) % 360.0) - 180.0)
  if length <= _EPS and rot_delta > _EPS:
    raise NotImplementedError(
      "sample_linear_path: pure rotation (no translation) is not supported. "
      f"Translation length={length:.4f} mm, rotation delta={rot_delta:.2f}°. "
      "Use move_to_joint_position with the target wrist angle for "
      "orientation-only changes."
    )

  # Cartesian arc-length trapezoid. Yaw rides the same s/length ratio so
  # rotation lands together with translation.
  s_seq = _arc_length_trapezoid_times(length, vel_mm_per_s, accel_mm_per_s2, dt_s)

  prev_for_snap: Optional[Dict[Axis, float]] = (
    dict(current_joints) if current_joints is not None else None
  )
  poses: List[Dict[Axis, float]] = []
  for s in s_seq:
    alpha = (s / length) if length > 0 else 1.0
    pose_i = CartesianPose(
      location=Coordinate(
        x=sx + alpha * (ex - sx),
        y=sy + alpha * (ey - sy),
        z=sz + alpha * (ez - sz),
      ),
      rotation=Rotation(z=_yaw_lerp(start_yaw, end_yaw, alpha)),
    )
    joints = ik(pose_i, cfg, gripper_params)
    if prev_for_snap is not None:
      joints = snap_to_current(joints, prev_for_snap)
    poses.append(joints)
    prev_for_snap = joints

  # Encoder-space sequence per axis. Elbow's encoder is angle-driven (sine
  # linkage), so convert position to angle before scaling by motor factor.
  enc_pos: Dict[Axis, List[int]] = {ax: [] for ax in _LINEAR_PATH_AXES}
  for joints in poses:
    for ax in _LINEAR_PATH_AXES:
      conv = cfg.axes[ax].motor_conversion_factor
      val = joints[ax]
      if ax is Axis.ELBOW:
        val = convert_elbow_position_to_angle(cfg, val)
      enc_pos[ax].append(int(round(val * conv)))

  # Central finite difference for velocity; one-sided at endpoints. The
  # trajectory is dt-aligned with a trailing hold sample (s = length),
  # so the one-sided FD at the last point is naturally zero — no need
  # to force it. Forcing zero against a non-trivial v[n-2] would create
  # the cubic-Hermite overshoot the trailing hold is meant to avoid.
  n = len(poses)
  # V[0] forced to 0 (matches C# MotorsMovePath line 4242). Some drives reject
  # the first preload frame if it has non-zero velocity.
  enc_vel: Dict[Axis, List[int]] = {ax: [0] * n for ax in _LINEAR_PATH_AXES}
  for ax in _LINEAR_PATH_AXES:
    seq = enc_pos[ax]
    if n >= 2:
      for i in range(1, n - 1):
        enc_vel[ax][i] = int(round((seq[i + 1] - seq[i - 1]) / (2.0 * dt_s)))
      enc_vel[ax][n - 1] = int(round((seq[n - 1] - seq[n - 2]) / dt_s))

  out: List[LinearPathSample] = []
  for i in range(n):
    out.append(LinearPathSample(
      time_s=i * dt_s,
      joints=poses[i],
      encoder_position={ax: enc_pos[ax][i] for ax in _LINEAR_PATH_AXES},
      encoder_velocity={ax: enc_vel[ax][i] for ax in _LINEAR_PATH_AXES},
    ))
  return out


def _build_samples_from_joints(
  joints_seq: List[Dict[Axis, float]],
  cfg: KX2Config,
  dt_s: float,
) -> List[LinearPathSample]:
  """Convert a joint-space sequence into encoder-domain PVT samples.

  Mirrors the encoder + central-diff velocity block of ``sample_linear_path``.
  Shared by every sampler so they all produce identically-formed samples.
  """
  n = len(joints_seq)
  enc_pos: Dict[Axis, List[int]] = {ax: [] for ax in _LINEAR_PATH_AXES}
  for joints in joints_seq:
    for ax in _LINEAR_PATH_AXES:
      conv = cfg.axes[ax].motor_conversion_factor
      val = joints[ax]
      if ax is Axis.ELBOW:
        val = convert_elbow_position_to_angle(cfg, val)
      enc_pos[ax].append(int(round(val * conv)))
  # V[0]=0 (matches sample_linear_path and C# MotorsMovePath line 4242).
  # Forward-diff initial velocity makes drives reject the first preload frame
  # in some configurations (queue_full on first write).
  enc_vel: Dict[Axis, List[int]] = {ax: [0] * n for ax in _LINEAR_PATH_AXES}
  for ax in _LINEAR_PATH_AXES:
    seq = enc_pos[ax]
    if n >= 2:
      for i in range(1, n - 1):
        enc_vel[ax][i] = int(round((seq[i + 1] - seq[i - 1]) / (2.0 * dt_s)))
      enc_vel[ax][n - 1] = int(round((seq[n - 1] - seq[n - 2]) / dt_s))
  return [
    LinearPathSample(
      time_s=i * dt_s,
      joints=joints_seq[i],
      encoder_position={ax: enc_pos[ax][i] for ax in _LINEAR_PATH_AXES},
      encoder_velocity={ax: enc_vel[ax][i] for ax in _LINEAR_PATH_AXES},
    ) for i in range(n)
  ]


def _ik_pose_sequence(
  poses: List[CartesianPose],
  cfg: KX2Config,
  gripper_params: GripperParams,
  current_joints: Optional[Dict[Axis, float]],
) -> List[Dict[Axis, float]]:
  """IK every pose, snap rotary axes to the previous solution (avoiding 360°
  unwinds across the trajectory). Raises IKError on the first out-of-range
  pose with the trajectory-relative index in the message."""
  prev: Optional[Dict[Axis, float]] = (
    dict(current_joints) if current_joints is not None else None
  )
  out: List[Dict[Axis, float]] = []
  for i, p in enumerate(poses):
    try:
      joints = ik(p, cfg, gripper_params)
    except IKError as e:
      raise IKError(f"sample {i}/{len(poses)}: {e}") from e
    if prev is not None:
      joints = snap_to_current(joints, prev)
    out.append(joints)
    prev = joints
  return out


def sample_parametric_path(
  cfg: KX2Config,
  gripper_params: GripperParams,
  path_fn: "Callable[[float], CartesianPose]",
  duration_s: float,
  dt_s: float,
  current_joints: Optional[Dict[Axis, float]] = None,
) -> List[LinearPathSample]:
  """Evaluate ``path_fn`` at every ``i * dt_s`` for ``i = 0..N`` (N chosen so
  the last sample lands at or just past ``duration_s``), IK each pose, and
  return encoder-domain samples ready for streaming.

  Caller owns the velocity profile — ``path_fn`` defines both shape and pacing.
  IK enforces joint limits; any out-of-range pose raises IKError with the
  sample index.
  """
  if duration_s <= 0 or dt_s <= 0:
    raise ValueError(f"duration_s and dt_s must be > 0 (got {duration_s}, {dt_s})")
  n = max(2, int(duration_s / dt_s) + 1)
  poses = [path_fn(min(i * dt_s, duration_s)) for i in range(n)]
  joints_seq = _ik_pose_sequence(poses, cfg, gripper_params, current_joints)
  return _build_samples_from_joints(joints_seq, cfg, dt_s)


def sample_waypoint_path(
  cfg: KX2Config,
  gripper_params: GripperParams,
  waypoints: List[CartesianPose],
  speed_mm_per_s: float,
  accel_mm_per_s2: float,
  dt_s: float,
  current_joints: Optional[Dict[Axis, float]] = None,
) -> List[LinearPathSample]:
  """Sample a Catmull-Rom spline through ``waypoints`` at the IPM cadence.

  Curve geometry: centripetal Catmull-Rom through the location component of
  every waypoint, with C¹ continuity at interior knots. Yaw is interpolated
  linearly between knot rotations (short-way around the circle). Endpoint
  tangents are extrapolated from the first/last segment.

  Time parametrization: trapezoidal velocity profile over the spline's total
  arc length, with peak speed ``speed_mm_per_s`` and peak acceleration
  ``accel_mm_per_s2``. Returns to a triangular profile when the spline is too
  short to reach peak speed.

  The spline is dense-sampled internally (50 sub-steps per segment) to build
  an arc-length lookup table; the trajectory is then sampled by stepping
  through that table at dt-spaced arc-length increments.
  """
  if len(waypoints) < 2:
    raise ValueError(f"need >= 2 waypoints, got {len(waypoints)}")
  if speed_mm_per_s <= 0 or accel_mm_per_s2 <= 0 or dt_s <= 0:
    raise ValueError(
      f"speed, accel, dt_s must be > 0 (got {speed_mm_per_s}, "
      f"{accel_mm_per_s2}, {dt_s})"
    )

  # Build extended control point list with mirrored endpoint tangents so the
  # Catmull-Rom evaluator can use four consecutive points at every segment.
  pts = [(w.location.x, w.location.y, w.location.z) for w in waypoints]
  yaws = [w.rotation.z for w in waypoints]
  ext = [tuple(2 * a - b for a, b in zip(pts[0], pts[1]))]
  ext.extend(pts)
  ext.append(tuple(2 * a - b for a, b in zip(pts[-1], pts[-2])))

  def cr_eval(seg_idx: int, t: float) -> "Tuple[float, float, float]":
    # Centripetal Catmull-Rom at local parameter t in [0, 1] within segment
    # seg_idx (which goes between waypoints[seg_idx] and waypoints[seg_idx+1]).
    p0, p1, p2, p3 = ext[seg_idx], ext[seg_idx + 1], ext[seg_idx + 2], ext[seg_idx + 3]
    t2 = t * t
    t3 = t2 * t
    return tuple(
      0.5 * (
        (2 * p1[k])
        + (-p0[k] + p2[k]) * t
        + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3
      ) for k in range(3)
    )

  # Build (cumulative arc length → (segment, local t)) table by densely
  # sampling the spline.
  SUBSTEPS = 50
  arc_table: List[Tuple[float, int, float]] = []  # (s, seg, local_t)
  total_len = 0.0
  prev_pt = pts[0]
  arc_table.append((0.0, 0, 0.0))
  for seg in range(len(pts) - 1):
    for k in range(1, SUBSTEPS + 1):
      t_local = k / SUBSTEPS
      p = cr_eval(seg, t_local)
      total_len += sqrt(sum((p[i] - prev_pt[i]) ** 2 for i in range(3)))
      arc_table.append((total_len, seg, t_local))
      prev_pt = p

  if total_len <= _EPS:
    # All waypoints colinear at one point — bail out with a hold-in-place
    # 2-sample trajectory so the streamer no-ops cleanly.
    one_pose = waypoints[0]
    return sample_parametric_path(
      cfg=cfg, gripper_params=gripper_params,
      path_fn=lambda _t: one_pose, duration_s=dt_s, dt_s=dt_s,
      current_joints=current_joints,
    )

  # Trapezoidal time → arc-length profile.
  s_seq = _arc_length_trapezoid_times(total_len, speed_mm_per_s, accel_mm_per_s2, dt_s)
  n = len(s_seq)

  # Resolve each arc-length s into (seg, local_t) via binary search in the table.
  poses: List[CartesianPose] = []
  for s in s_seq:
    s_clamped = min(max(s, 0.0), total_len)
    lo, hi = 0, len(arc_table) - 1
    while hi - lo > 1:
      mid = (lo + hi) // 2
      if arc_table[mid][0] <= s_clamped:
        lo = mid
      else:
        hi = mid
    s0, seg0, t0 = arc_table[lo]
    s1, seg1, t1 = arc_table[hi]
    span = s1 - s0
    alpha = 0.0 if span <= _EPS else (s_clamped - s0) / span
    # Linear interpolation in local-t space within the same segment; if the
    # table spans a segment boundary, evaluate using the higher one.
    if seg0 == seg1:
      seg = seg0
      tl = t0 + alpha * (t1 - t0)
    else:
      seg = seg1
      tl = t1 * alpha
    x, y, z = cr_eval(seg, tl)
    # Yaw interpolation: chord-length parameter along the full polyline so
    # yaw lerps across segment boundaries naturally.
    yaw_alpha = s_clamped / total_len
    # Map yaw_alpha (0..1 over whole polyline) onto waypoint indices linearly.
    yaw_pos = yaw_alpha * (len(yaws) - 1)
    yi = min(int(yaw_pos), len(yaws) - 2)
    yf = yaw_pos - yi
    yaw = _yaw_lerp(yaws[yi], yaws[yi + 1], yf)
    poses.append(CartesianPose(location=Coordinate(x=x, y=y, z=z),
                               rotation=Rotation(z=yaw)))

  joints_seq = _ik_pose_sequence(poses, cfg, gripper_params, current_joints)
  return _build_samples_from_joints(joints_seq, cfg, dt_s)
