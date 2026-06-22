"""
PF400 kinematics: FK and IK for a 4-DOF SCARA + prismatic Z + optional rail.

Joint dict keys match the firmware and `Axis` enum:
  1: J1 (Z lift) [mm]
  2: J2 (shoulder) [deg]
  3: J3 (elbow) [deg]
  4: J4 (wrist) [deg]
  6: rail position [mm] (optional; 0 if missing)

Task pose p = (x, y, z, yaw) with yaw in degrees. The gripper stays level
(all revolute axes parallel to world +Z), so IK is closed-form:
Z is decoupled, planar 2R for (x, y), wrist yaw for orientation.

Sign conventions follow right-hand rule about +Z (CCW positive looking down).
"""

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, pi, radians, sin
from typing import Literal, Optional, Tuple

from pylabrobot.capabilities.arms.standard import CartesianPose, JointPose
from pylabrobot.resources import Coordinate, Rotation


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


ElbowOrientation = Literal["right", "left"]
Wrist = Literal["cw", "ccw"]


@dataclass
class PreciseFlexCartesianPose(CartesianPose):
  rail_position: Optional[float] = None
  orientation: Optional[ElbowOrientation] = None
  wrist: Optional[Wrist] = None


@dataclass(frozen=True)
class WorkingVolume:
  """Reachable tool-tip envelope: an annulus about the shoulder, over a Z range (mm)."""

  inner: float
  outer: float
  zmin: float
  zmax: float


# ---------------------------------------------------------------------------
# Kinematic parameters
# ---------------------------------------------------------------------------


# Known PF400 link-length configs (l1 = shoulder->elbow, l2 = elbow->wrist), in mm, per the 615287
# System Dimensions - the single source of truth for the standard vs extended arm.
ARM_LINKS_STANDARD = (225.0, 210.0)
ARM_LINKS_EXTENDED = (302.0, 289.0)
_LINK_MATCH_TOLERANCE = 5.0  # mm; per-link calibration spread allowed when matching a read


@dataclass
class PF400Params:
  """Calibrated link lengths; sub-mm FK residual on a held-out probe set."""

  l1: float = ARM_LINKS_EXTENDED[0]  # shoulder -> elbow [mm]
  l2: float = ARM_LINKS_EXTENDED[1]  # elbow -> wrist [mm]
  gripper_length: float = 162.0  # wrist -> TCP [mm]
  gripper_z_offset: float = 0.0
  eps: float = 1e-6


def _classify_pf400_reach(links: Tuple[float, float]) -> Literal["standard", "extended", "unknown"]:
  """Classify (l1, l2) link lengths as the standard or extended PF400 arm, or "unknown".

  "unknown" means the lengths match neither known config - a sign the arm's device-stored link
  lengths may have been changed.

  Args:
    links: (l1, l2) link lengths in mm (inner shoulder -> elbow, outer elbow -> wrist).
  Returns:
    "standard", "extended", or "unknown".
  """
  l1, l2 = links
  tol = _LINK_MATCH_TOLERANCE
  if abs(l1 - ARM_LINKS_STANDARD[0]) <= tol and abs(l2 - ARM_LINKS_STANDARD[1]) <= tol:
    return "standard"
  if abs(l1 - ARM_LINKS_EXTENDED[0]) <= tol and abs(l2 - ARM_LINKS_EXTENDED[1]) <= tol:
    return "extended"
  return "unknown"


# ---------------------------------------------------------------------------
# Forward / inverse kinematics
# ---------------------------------------------------------------------------


class IKError(ValueError):
  """Target pose is unreachable."""


def fk(joints: JointPose, p: PF400Params) -> PreciseFlexCartesianPose:
  """Forward kinematics.

  Args:
    joints: {1: J1 mm, 2: J2 deg, 3: J3 deg, 4: J4 deg, 6: rail position mm (optional)}.
    p: kinematic parameters.
  Returns:
    PreciseFlexCartesianPose with location, rotation.yaw, rail_position, and
    orientation/wrist derived from the joint configuration (J3 sign and
    wrapped J4 sign, respectively).
  """
  j1 = joints[1]
  j2 = radians(joints[2])
  j3 = radians(joints[3])
  j4 = radians(joints[4])
  rail_position = joints.get(6, 0.0)
  yaw = j2 + j3 + j4
  x = rail_position + p.l1 * cos(j2) + p.l2 * cos(j2 + j3) + p.gripper_length * cos(yaw)
  y = p.l1 * sin(j2) + p.l2 * sin(j2 + j3) + p.gripper_length * sin(yaw)
  z = j1 + p.gripper_z_offset
  j3_wrapped = (joints[3] + 180) % 360 - 180
  orientation = "right" if j3_wrapped >= 0 else "left"
  wrist = "ccw" if joints[4] >= 0 else "cw"
  return PreciseFlexCartesianPose(
    location=Coordinate(x, y, z),
    rotation=Rotation(-180, 90, z=degrees(yaw)),
    orientation=orientation,
    wrist=wrist,
    rail_position=rail_position,
  )


def ik(pose: PreciseFlexCartesianPose, p: PF400Params) -> JointPose:
  """Inverse kinematics.

  Args:
    pose: PreciseFlexCartesianPose. Requires location.{x,y,z}, rotation.yaw,
      orientation ("right"/"left" — elbow branch), wrist ("cw"/"ccw" —
      absolute J4 sign), and rail_position (mm, arm's X origin).
    p: kinematic parameters.
  Returns:
    joints dict {1: J1 mm, 2: J2 deg, 3: J3 deg, 4: J4 deg, 6: rail position mm}.
    J4 is in (-360°, 0°] for wrist="cw" and [0°, 360°) for wrist="ccw"
    (J4=0 qualifies for both).
  Raises:
    IKError if the target is unreachable or the wrist coincides with the
    shoulder axis (singularity where the shoulder angle is undefined).
  """
  if pose.orientation not in ("right", "left"):
    raise ValueError(f"pose.orientation must be 'right' or 'left', got {pose.orientation!r}")
  if pose.wrist not in ("cw", "ccw"):
    raise ValueError(f"pose.wrist must be 'cw' or 'ccw', got {pose.wrist!r}")
  if pose.rail_position is None:
    raise ValueError("pose.rail_position must be set")
  yaw = radians(pose.rotation.yaw)

  # Shoulder is at (pose.rail_position, 0) in world; work in shoulder-centered coords.
  x_w = pose.location.x - pose.rail_position - p.gripper_length * cos(yaw)
  y_w = pose.location.y - p.gripper_length * sin(yaw)

  r = hypot(x_w, y_w)
  r_max = p.l1 + p.l2
  r_min = abs(p.l1 - p.l2)
  if r > r_max + p.eps or r < r_min - p.eps:
    raise IKError(f"wrist target r={r:.3f} mm outside annulus [{r_min:.3f}, {r_max:.3f}]")
  if r < p.eps:
    raise IKError("wrist target coincides with shoulder axis (singular)")

  c_elbow = (r * r - p.l1 * p.l1 - p.l2 * p.l2) / (2.0 * p.l1 * p.l2)
  c_elbow = max(-1.0, min(1.0, c_elbow))
  s_elbow = (1 if pose.orientation == "right" else -1) * (1.0 - c_elbow * c_elbow) ** 0.5
  elbow_delta = atan2(s_elbow, c_elbow)
  alpha = atan2(y_w, x_w) - atan2(p.l2 * s_elbow, p.l1 + p.l2 * c_elbow)

  j2 = _wrap(alpha)
  j3 = _wrap(elbow_delta)
  j4 = _wrap(yaw - alpha - elbow_delta)
  # Tolerance on the sign check so J4 values within FP dust of 0 aren't
  # pushed to ±2π; J4 ≈ 0 satisfies both conventions.
  if pose.wrist == "cw" and j4 > p.eps:
    j4 -= 2 * pi
  elif pose.wrist == "ccw" and j4 < -p.eps:
    j4 += 2 * pi

  return {
    1: pose.location.z - p.gripper_z_offset,
    2: degrees(j2),
    3: degrees(j3),
    4: degrees(j4),
    6: pose.rail_position,
  }


def _wrap(a: float) -> float:
  """Wrap angle to (-pi, pi]."""
  return (a + pi) % (2 * pi) - pi
