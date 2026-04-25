import math
import unittest

from pylabrobot.paa.kx2 import kinematics
from pylabrobot.paa.kx2.config import Axis, AxisConfig, KX2Config
from pylabrobot.paa.kx2.driver import JointMoveDirection
from pylabrobot.paa.kx2.kinematics import IKError, KX2GripperLocation
from pylabrobot.resources import Coordinate, Rotation


def _axis() -> AxisConfig:
  return AxisConfig(
    motor_conversion_factor=1.0,
    max_travel=180.0,
    min_travel=-180.0,
    unlimited_travel=False,
    absolute_encoder=True,
    max_vel=100.0,
    max_accel=100.0,
    joint_move_direction=JointMoveDirection.Normal,
    digital_inputs={},
    analog_inputs={},
    outputs={},
  )


def _config(
  wrist_offset: float = 10.0,
  elbow_offset: float = 20.0,
  elbow_zero_offset: float = 5.0,
  gripper_length: float = 15.0,
  gripper_z_offset: float = 3.0,
  gripper_finger_side: str = "barcode_reader",
) -> KX2Config:
  return KX2Config(
    wrist_offset=wrist_offset,
    elbow_offset=elbow_offset,
    elbow_zero_offset=elbow_zero_offset,
    gripper_length=gripper_length,
    gripper_z_offset=gripper_z_offset,
    gripper_finger_side=gripper_finger_side,  # type: ignore[arg-type]
    axes={a: _axis() for a in (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)},
    base_to_gripper_clearance_z=0.0,
    base_to_gripper_clearance_arm=0.0,
    robot_on_rail=False,
    servo_gripper=None,
  )


def _approx(a: float, b: float, eps: float = 1e-9) -> bool:
  return abs(a - b) < eps


class FKIKRoundTrip(unittest.TestCase):
  def test_roundtrip_ccw(self):
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=100, y=200, z=50),
      rotation=Rotation(z=30),
      wrist="ccw",
    )
    joints = kinematics.ik(pose, c)
    back = kinematics.fk(joints, c)
    self.assertAlmostEqual(back.location.x, pose.location.x, places=9)
    self.assertAlmostEqual(back.location.y, pose.location.y, places=9)
    self.assertAlmostEqual(back.location.z, pose.location.z, places=9)
    self.assertAlmostEqual(back.rotation.z, pose.rotation.z, places=9)

  def test_roundtrip_cw_yields_same_world_pose(self):
    """cw and ccw produce J4 360° apart but represent the same physical pose."""
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=100, y=200, z=50), rotation=Rotation(z=30), wrist="cw"
    )
    j_cw = kinematics.ik(pose, c)
    j_ccw = kinematics.ik(KX2GripperLocation(**{**pose.__dict__, "wrist": "ccw"}), c)
    self.assertAlmostEqual(j_ccw[Axis.WRIST] - j_cw[Axis.WRIST], 360.0, places=9)
    # FK on either should land at the original pose.
    for j in (j_cw, j_ccw):
      back = kinematics.fk(j, c)
      self.assertAlmostEqual(back.location.x, pose.location.x, places=9)
      self.assertAlmostEqual(back.location.y, pose.location.y, places=9)
      self.assertAlmostEqual(back.rotation.z, pose.rotation.z, places=9)

  def test_roundtrip_at_origin_yaw_zero(self):
    """Sanity: a pose at (0, R, Z, 0°) lands shoulder=0°."""
    c = _config(wrist_offset=0, elbow_offset=0, elbow_zero_offset=0, gripper_length=0,
                gripper_z_offset=0)
    pose = KX2GripperLocation(
      location=Coordinate(x=0, y=300, z=10), rotation=Rotation(z=0), wrist="ccw"
    )
    joints = kinematics.ik(pose, c)
    self.assertAlmostEqual(joints[Axis.SHOULDER], 0.0, places=9)
    self.assertAlmostEqual(joints[Axis.ELBOW], 300.0, places=9)
    self.assertAlmostEqual(joints[Axis.Z], 10.0, places=9)
    self.assertAlmostEqual(joints[Axis.WRIST], 0.0, places=9)


class IKWristSign(unittest.TestCase):
  def test_cw_yields_non_positive_wrist(self):
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=50, y=300, z=20), rotation=Rotation(z=45), wrist="cw"
    )
    joints = kinematics.ik(pose, c)
    self.assertLessEqual(joints[Axis.WRIST], c.eps)

  def test_ccw_yields_non_negative_wrist(self):
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=50, y=300, z=20), rotation=Rotation(z=45), wrist="ccw"
    )
    joints = kinematics.ik(pose, c)
    self.assertGreaterEqual(joints[Axis.WRIST], -c.eps)

  def test_wrist_near_zero_satisfies_both(self):
    """When the natural J4 is exactly 0, neither cw nor ccw should shift it."""
    c = _config()
    # Construct a pose where wrist - shoulder = 0 naturally:
    # rotation.z == shoulder. Pick rotation.z = -atan2(x, y) in degrees.
    x, y = 100.0, 100.0
    yaw_deg = -math.degrees(math.atan2(x, y))  # ≈ -45°
    pose = KX2GripperLocation(
      location=Coordinate(x=x, y=y, z=0),
      rotation=Rotation(z=yaw_deg),
      wrist="ccw",
    )
    # Use zero gripper offsets so location maps directly to wrist axis.
    c0 = _config(gripper_length=0, gripper_z_offset=0)
    joints = kinematics.ik(pose, c0)
    self.assertAlmostEqual(joints[Axis.WRIST], 0.0, places=6)

    pose_cw = KX2GripperLocation(**{**pose.__dict__, "wrist": "cw"})
    joints_cw = kinematics.ik(pose_cw, c0)
    self.assertAlmostEqual(joints_cw[Axis.WRIST], 0.0, places=6)


class IKErrors(unittest.TestCase):
  def test_none_wrist_raises_valueerror(self):
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=0, y=100, z=0), rotation=Rotation(z=0), wrist=None
    )
    with self.assertRaises(ValueError):
      kinematics.ik(pose, c)

  def test_invalid_wrist_string_raises_valueerror(self):
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=0, y=100, z=0), rotation=Rotation(z=0), wrist="up",  # type: ignore
    )
    with self.assertRaises(ValueError):
      kinematics.ik(pose, c)

  def test_x_rotation_raises_ikerror(self):
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=0, y=100, z=0),
      rotation=Rotation(x=10, z=0),
      wrist="ccw",
    )
    with self.assertRaises(IKError):
      kinematics.ik(pose, c)

  def test_y_rotation_raises_ikerror(self):
    c = _config()
    pose = KX2GripperLocation(
      location=Coordinate(x=0, y=100, z=0),
      rotation=Rotation(y=10, z=0),
      wrist="ccw",
    )
    with self.assertRaises(IKError):
      kinematics.ik(pose, c)


class FKResult(unittest.TestCase):
  def test_fk_sets_wrist_field_from_j4_sign(self):
    c = _config()
    j_pos = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 100.0, Axis.WRIST: 45.0}
    self.assertEqual(kinematics.fk(j_pos, c).wrist, "ccw")
    j_neg = {**j_pos, Axis.WRIST: -45.0}
    self.assertEqual(kinematics.fk(j_neg, c).wrist, "cw")
    j_zero = {**j_pos, Axis.WRIST: 0.0}
    self.assertEqual(kinematics.fk(j_zero, c).wrist, "ccw")  # 0 ≥ 0 -> ccw


class SnapToCurrent(unittest.TestCase):
  def test_shifts_wrist_toward_current(self):
    """J4=20 with current=380 should snap to 380 (closer than 20)."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 20.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 380.0}
    out = kinematics.snap_to_current(j, cur, "ccw")
    self.assertAlmostEqual(out[Axis.WRIST], 380.0)

  def test_shifts_shoulder_toward_current(self):
    """Shoulder is rotary too — also snaps."""
    j = {Axis.SHOULDER: -170.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    cur = {Axis.SHOULDER: 200.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur, "ccw")
    self.assertAlmostEqual(out[Axis.SHOULDER], 190.0)  # -170 + 360

  def test_prismatic_axes_pass_through(self):
    """Z and elbow are not 360°-modulo; they should be untouched."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 100.0, Axis.ELBOW: 50.0, Axis.WRIST: 0.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 999.0, Axis.ELBOW: 999.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur, "ccw")
    self.assertEqual(out[Axis.Z], 100.0)
    self.assertEqual(out[Axis.ELBOW], 50.0)

  def test_re_enforces_ccw_after_snap(self):
    """Explicit ccw: snap might land J4 negative; ccw re-enforce shifts +360."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 350.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur, "ccw")
    # snap shifts 350 -> -10 (toward current 0); ccw re-enforce -> +350
    self.assertAlmostEqual(out[Axis.WRIST], 350.0)

  def test_re_enforces_cw_after_snap(self):
    """Symmetric: explicit cw re-enforce shifts a positive J4 negative."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: -350.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur, "cw")
    # snap shifts -350 -> 10 (toward current 0); cw re-enforce -> -350
    self.assertAlmostEqual(out[Axis.WRIST], -350.0)

  def test_closest_mode_does_not_re_enforce(self):
    """wrist=None means closest: trust the snap, don't shift back."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 350.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur, None)
    # Pure shift toward current: 350 - 360 = -10. No re-enforce.
    self.assertAlmostEqual(out[Axis.WRIST], -10.0)

  def test_no_shift_when_already_close(self):
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 30.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 25.0}
    out = kinematics.snap_to_current(j, cur, "ccw")
    self.assertAlmostEqual(out[Axis.WRIST], 30.0)


class GripperFingerSide(unittest.TestCase):
  def test_proximity_side_negates_gripper_offset(self):
    """Same joints, opposite finger side -> clamp point reflected through wrist axis."""
    c_bc = _config(gripper_finger_side="barcode_reader")
    c_pr = _config(gripper_finger_side="proximity_sensor")
    j = {Axis.SHOULDER: 30.0, Axis.Z: 50.0, Axis.ELBOW: 100.0, Axis.WRIST: 15.0}
    p_bc = kinematics.fk(j, c_bc)
    p_pr = kinematics.fk(j, c_pr)

    # Wrist position is the midpoint between the two clamp points.
    wrist_x = (p_bc.location.x + p_pr.location.x) / 2
    wrist_y = (p_bc.location.y + p_pr.location.y) / 2
    yaw_deg = j[Axis.WRIST] + j[Axis.SHOULDER]
    yaw = math.radians(yaw_deg)
    self.assertAlmostEqual(
      p_bc.location.x - wrist_x, c_bc.gripper_length * math.sin(yaw), delta=1e-5
    )
    self.assertAlmostEqual(
      p_bc.location.y - wrist_y, -c_bc.gripper_length * math.cos(yaw), delta=1e-5
    )
    # z and yaw are independent of finger side.
    self.assertAlmostEqual(p_bc.location.z, p_pr.location.z, places=9)
    self.assertAlmostEqual(p_bc.rotation.z, p_pr.rotation.z, places=9)

  def test_proximity_roundtrip(self):
    c = _config(gripper_finger_side="proximity_sensor")
    pose = KX2GripperLocation(
      location=Coordinate(x=100, y=200, z=50),
      rotation=Rotation(z=30),
      wrist="ccw",
    )
    joints = kinematics.ik(pose, c)
    back = kinematics.fk(joints, c)
    self.assertAlmostEqual(back.location.x, pose.location.x, places=9)
    self.assertAlmostEqual(back.location.y, pose.location.y, places=9)
    self.assertAlmostEqual(back.location.z, pose.location.z, places=9)
    self.assertAlmostEqual(back.rotation.z, pose.rotation.z, places=9)

  def test_ik_elbow_differs_by_twice_gripper_length(self):
    """For a clamp point on the +y axis with yaw=0, both sides give
    shoulder=0 but the wrist sits 2*gripper_length further out for
    barcode_reader (gripper points +y away from base) than for
    proximity_sensor (gripper points -y back toward base)."""
    pose = KX2GripperLocation(
      location=Coordinate(x=0, y=300, z=0), rotation=Rotation(z=0), wrist="ccw"
    )
    c_bc = _config(gripper_finger_side="barcode_reader")
    c_pr = _config(gripper_finger_side="proximity_sensor")
    j_bc = kinematics.ik(pose, c_bc)
    j_pr = kinematics.ik(pose, c_pr)
    self.assertAlmostEqual(j_bc[Axis.SHOULDER], 0.0, places=9)
    self.assertAlmostEqual(j_pr[Axis.SHOULDER], 0.0, places=9)
    self.assertAlmostEqual(j_bc[Axis.ELBOW] - j_pr[Axis.ELBOW], 2 * c_bc.gripper_length, places=9)


class ShoulderSnapAt180(unittest.TestCase):
  def test_negative_180_snaps_to_positive(self):
    """A pose pointing exactly along -y has shoulder = ±180; we snap to +180."""
    c = _config(wrist_offset=0, elbow_offset=0, elbow_zero_offset=0, gripper_length=0,
                gripper_z_offset=0)
    pose = KX2GripperLocation(
      location=Coordinate(x=0, y=-100, z=0), rotation=Rotation(z=180), wrist="ccw"
    )
    joints = kinematics.ik(pose, c)
    self.assertAlmostEqual(joints[Axis.SHOULDER], 180.0, places=9)


if __name__ == "__main__":
  unittest.main()
