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

from math import atan2, cos, degrees, hypot, radians, sin
from typing import Dict

from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.paa.kx2.config import Axis, GripperConfig, KX2Config
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
