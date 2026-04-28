"""
KX2 kinematics: FK and IK.

The KX2 is *not* a dual-link SCARA. The elbow is a prismatic radial slide
(not a revolute J3), Z is a separate prismatic axis, and the rail is
outside this kinematic model. So the math is closed-form and trivially
cheap — no two-link cosine law, no elbow-up/elbow-down branch, no
unreachable-pose check beyond "rotation must be about +Z".

Joint dict keys match the drive node-IDs and the `KX2ArmBackend.Axis` enum:
  1: shoulder [deg]
  2: Z [mm]
  3: elbow [mm] (radial extension)
  4: wrist [deg]

Task pose is a `KX2GripperLocation` (GripperLocation + a wrist-sign
convention). The gripper clamp point is in world coordinates; rotation.z
is yaw in degrees about world +Z, and rotation.x/y must be 0. Sign
convention follows right-hand rule about +Z (CCW positive looking down).

For "closest" semantics — minimizing arm motion between two poses — call
`snap_to_current` after `ik`.
"""

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, radians, sin
from typing import Dict, Literal, Optional

from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.paa.kx2.config import Axis, KX2Config
from pylabrobot.resources import Coordinate, Rotation


Wrist = Literal["cw", "ccw"]


@dataclass
class KX2GripperLocation(GripperLocation):
  """Gripper pose with an optional wrist-sign constraint.

  wrist:
    - "cw":  J4 ≤ 0 (negative), with a small tolerance near 0.
    - "ccw": J4 ≥ 0 (positive), with a small tolerance near 0.
    - None: unspecified. `ik` rejects None; backend wrappers fill it with
      the current joint's wrist sign so the arm picks whichever solution
      needs the least motion.
  """

  wrist: Optional[Wrist] = None


class IKError(ValueError):
  """Target pose is unreachable (for now: non-Z rotation requested)."""


def fk(joints: Dict[Axis, float], c: KX2Config) -> KX2GripperLocation:
  """Forward kinematics.

  Args:
    joints: {Axis.SHOULDER: deg, Axis.Z: mm, Axis.ELBOW: mm, Axis.WRIST: deg}.
    c: arm configuration.
  Returns:
    KX2GripperLocation with the gripper clamp point and a wrist sign
    derived from the joint configuration (J4 ≥ 0 → "ccw", else "cw").
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

  # Wrist -> gripper: inverse of the gripper -> wrist translation in ik,
  # so callers observe the gripper clamp point symmetric with what they
  # pass into ik. Sign tracks which finger is the radial "front".
  yaw = radians(yaw_deg)
  gl = c.gripper_length if c.gripper_finger_side == "barcode_reader" else -c.gripper_length
  gripper_x = wrist_x + gl * sin(yaw)
  gripper_y = wrist_y - gl * cos(yaw)
  gripper_z = wrist_z - c.gripper_z_offset

  return KX2GripperLocation(
    location=Coordinate(x=gripper_x, y=gripper_y, z=gripper_z),
    rotation=Rotation(z=yaw_deg),
    wrist="ccw" if joints[Axis.WRIST] >= 0 else "cw",
  )


def ik(pose: KX2GripperLocation, c: KX2Config) -> Dict[Axis, float]:
  """Inverse kinematics.

  Args:
    pose: KX2GripperLocation. Requires pose.wrist to be "cw" or "ccw" —
      "closest" semantics live in the backend wrapper (fill None with the
      current joint's sign, then call `snap_to_current` after).
    c: arm configuration.
  Returns:
    joints dict {Axis.SHOULDER: deg, Axis.Z: mm, Axis.ELBOW: mm, Axis.WRIST: deg}.
    J4 is in (-360°, 0°] when wrist="cw" and [0°, 360°) when wrist="ccw"
    (J4 ≈ 0 satisfies both, up to `c.eps`).

    Note: J4 is not constrained to wrist travel limits. The backend's
    `_cart_to_joints` calls `snap_to_current` immediately after, which
    pulls J4 toward the current (in-range) wrist position by 360°
    multiples. Direct callers of `ik` must handle travel range themselves.
  Raises:
    ValueError if pose.wrist is not "cw" or "ccw".
    IKError if the requested rotation has an x or y component.
  """
  if pose.wrist not in ("cw", "ccw"):
    raise ValueError(f"pose.wrist must be 'cw' or 'ccw', got {pose.wrist!r}")
  if pose.rotation.x != 0 or pose.rotation.y != 0:
    raise IKError("Only Z rotation is supported for KX2")

  # Gripper -> wrist: the incoming pose describes the gripper clamp point;
  # the joint-space math operates on the wrist axis. Rigid offset with the
  # gripper length on the radial axis (governed by world rotation z) and
  # the gripper z offset downward. Sign tracks which finger is the radial
  # "front".
  yaw = radians(pose.rotation.z)
  gl = c.gripper_length if c.gripper_finger_side == "barcode_reader" else -c.gripper_length
  x = pose.location.x - gl * sin(yaw)
  y = pose.location.y + gl * cos(yaw)
  wrist_z = pose.location.z + c.gripper_z_offset

  shoulder = -degrees(atan2(x, y))
  if abs(shoulder + 180.0) < c.eps:
    shoulder = 180.0

  elbow = hypot(x, y) - c.wrist_offset - c.elbow_offset - c.elbow_zero_offset

  wrist = pose.rotation.z - shoulder
  # Enforce requested sign. Tolerance on the check so J4 values within FP
  # dust of 0 aren't pushed to ±360; J4 ≈ 0 satisfies both conventions.
  if pose.wrist == "cw" and wrist > c.eps:
    wrist -= 360.0
  elif pose.wrist == "ccw" and wrist < -c.eps:
    wrist += 360.0

  return {Axis.SHOULDER: shoulder, Axis.Z: wrist_z, Axis.ELBOW: elbow, Axis.WRIST: wrist}


def snap_to_current(
  joints: Dict[Axis, float], current: Dict[Axis, float], wrist: Optional[Wrist] = None
) -> Dict[Axis, float]:
  """Shift rotary joints by 360° multiples toward `current`. Z and elbow are
  prismatic and pass through unchanged.

  If `wrist` is "cw" or "ccw", re-enforce that sign half on J4 after the
  shift (use this when the caller explicitly chose a sign and wants it
  preserved even at the cost of extra motion). If `wrist` is None, the
  shift is unconditional — the user wanted "closest", so trust the snap.
  """
  out = dict(joints)
  for axis in (Axis.SHOULDER, Axis.WRIST):
    out[axis] += 360 * round((current[axis] - out[axis]) / 360)
  if wrist == "ccw" and out[Axis.WRIST] < 0:
    out[Axis.WRIST] += 360
  elif wrist == "cw" and out[Axis.WRIST] > 0:
    out[Axis.WRIST] -= 360
  return out
