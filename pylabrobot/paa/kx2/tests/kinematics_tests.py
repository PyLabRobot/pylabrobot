import math
import unittest

from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.paa.kx2 import kinematics
from pylabrobot.paa.kx2.config import Axis, AxisConfig, GripperParams, KX2Config
from pylabrobot.paa.kx2.driver import JointMoveDirection
from pylabrobot.paa.kx2.kinematics import IKError
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
) -> KX2Config:
  return KX2Config(
    wrist_offset=wrist_offset,
    elbow_offset=elbow_offset,
    elbow_zero_offset=elbow_zero_offset,
    axes={a: _axis() for a in (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)},
    base_to_gripper_clearance_z=0.0,
    base_to_gripper_clearance_arm=0.0,
    robot_on_rail=False,
    servo_gripper=None,
  )


class FKIKRoundTrip(unittest.TestCase):
  def test_roundtrip(self):
    c = _config()
    g = GripperParams(length=15.0, z_offset=3.0)
    pose = GripperLocation(
      location=Coordinate(x=100, y=200, z=50), rotation=Rotation(z=30)
    )
    joints = kinematics.ik(pose, c, g)
    back = kinematics.fk(joints, c, g)
    self.assertAlmostEqual(back.location.x, pose.location.x, places=9)
    self.assertAlmostEqual(back.location.y, pose.location.y, places=9)
    self.assertAlmostEqual(back.location.z, pose.location.z, places=9)
    self.assertAlmostEqual(back.rotation.z, pose.rotation.z, places=9)

  def test_roundtrip_at_origin_yaw_zero(self):
    """Sanity: a pose at (0, R, Z, 0°) lands shoulder=0°."""
    c = _config(wrist_offset=0, elbow_offset=0, elbow_zero_offset=0)
    g = GripperParams()
    pose = GripperLocation(
      location=Coordinate(x=0, y=300, z=10), rotation=Rotation(z=0)
    )
    joints = kinematics.ik(pose, c, g)
    self.assertAlmostEqual(joints[Axis.SHOULDER], 0.0, places=9)
    self.assertAlmostEqual(joints[Axis.ELBOW], 300.0, places=9)
    self.assertAlmostEqual(joints[Axis.Z], 10.0, places=9)
    self.assertAlmostEqual(joints[Axis.WRIST], 0.0, places=9)


class IKErrors(unittest.TestCase):
  def test_x_rotation_raises_ikerror(self):
    c = _config()
    g = GripperParams()
    pose = GripperLocation(
      location=Coordinate(x=0, y=100, z=0), rotation=Rotation(x=10, z=0)
    )
    with self.assertRaises(IKError):
      kinematics.ik(pose, c, g)

  def test_y_rotation_raises_ikerror(self):
    c = _config()
    g = GripperParams()
    pose = GripperLocation(
      location=Coordinate(x=0, y=100, z=0), rotation=Rotation(y=10, z=0)
    )
    with self.assertRaises(IKError):
      kinematics.ik(pose, c, g)


class SnapToCurrent(unittest.TestCase):
  def test_shifts_wrist_toward_current(self):
    """J4=20 with current=380 should snap to 380 (closer than 20)."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 20.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 380.0}
    out = kinematics.snap_to_current(j, cur)
    self.assertAlmostEqual(out[Axis.WRIST], 380.0)

  def test_shifts_shoulder_toward_current(self):
    """Shoulder is rotary too — also snaps."""
    j = {Axis.SHOULDER: -170.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    cur = {Axis.SHOULDER: 200.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur)
    self.assertAlmostEqual(out[Axis.SHOULDER], 190.0)  # -170 + 360

  def test_prismatic_axes_pass_through(self):
    """Z and elbow are not 360°-modulo; they should be untouched."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 100.0, Axis.ELBOW: 50.0, Axis.WRIST: 0.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 999.0, Axis.ELBOW: 999.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur)
    self.assertEqual(out[Axis.Z], 100.0)
    self.assertEqual(out[Axis.ELBOW], 50.0)

  def test_pure_closest_no_re_enforce(self):
    """The wrist drive wraps freely — snap pulls to whichever 360° wrap is closest."""
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 350.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0}
    out = kinematics.snap_to_current(j, cur)
    self.assertAlmostEqual(out[Axis.WRIST], -10.0)

  def test_no_shift_when_already_close(self):
    j = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 30.0}
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 25.0}
    out = kinematics.snap_to_current(j, cur)
    self.assertAlmostEqual(out[Axis.WRIST], 30.0)


class GripperFingerSide(unittest.TestCase):
  def test_proximity_side_negates_gripper_offset(self):
    """Same joints, opposite finger side -> clamp point reflected through wrist axis."""
    c = _config()
    g_bc = GripperParams(length=15.0, z_offset=3.0, finger_side="barcode_reader")
    g_pr = GripperParams(length=15.0, z_offset=3.0, finger_side="proximity_sensor")
    j = {Axis.SHOULDER: 30.0, Axis.Z: 50.0, Axis.ELBOW: 100.0, Axis.WRIST: 15.0}
    p_bc = kinematics.fk(j, c, g_bc)
    p_pr = kinematics.fk(j, c, g_pr)

    # Wrist position is the midpoint between the two clamp points.
    wrist_x = (p_bc.location.x + p_pr.location.x) / 2
    wrist_y = (p_bc.location.y + p_pr.location.y) / 2
    yaw_deg = j[Axis.WRIST] + j[Axis.SHOULDER]
    yaw = math.radians(yaw_deg)
    self.assertAlmostEqual(
      p_bc.location.x - wrist_x, g_bc.length * math.sin(yaw), delta=1e-5
    )
    self.assertAlmostEqual(
      p_bc.location.y - wrist_y, -g_bc.length * math.cos(yaw), delta=1e-5
    )
    self.assertAlmostEqual(p_bc.location.z, p_pr.location.z, places=9)
    self.assertAlmostEqual(p_bc.rotation.z, p_pr.rotation.z, places=9)

  def test_proximity_roundtrip(self):
    c = _config()
    g = GripperParams(length=15.0, z_offset=3.0, finger_side="proximity_sensor")
    pose = GripperLocation(
      location=Coordinate(x=100, y=200, z=50), rotation=Rotation(z=30)
    )
    joints = kinematics.ik(pose, c, g)
    back = kinematics.fk(joints, c, g)
    self.assertAlmostEqual(back.location.x, pose.location.x, places=9)
    self.assertAlmostEqual(back.location.y, pose.location.y, places=9)
    self.assertAlmostEqual(back.location.z, pose.location.z, places=9)
    self.assertAlmostEqual(back.rotation.z, pose.rotation.z, places=9)

  def test_ik_elbow_differs_by_twice_gripper_length(self):
    """For a clamp point on the +y axis with yaw=0, both sides give
    shoulder=0 but the wrist sits 2*gripper_length further out for
    barcode_reader (gripper points +y away from base) than for
    proximity_sensor (gripper points -y back toward base)."""
    pose = GripperLocation(
      location=Coordinate(x=0, y=300, z=0), rotation=Rotation(z=0)
    )
    c = _config()
    g_bc = GripperParams(length=15.0, z_offset=3.0, finger_side="barcode_reader")
    g_pr = GripperParams(length=15.0, z_offset=3.0, finger_side="proximity_sensor")
    j_bc = kinematics.ik(pose, c, g_bc)
    j_pr = kinematics.ik(pose, c, g_pr)
    self.assertAlmostEqual(j_bc[Axis.SHOULDER], 0.0, places=9)
    self.assertAlmostEqual(j_pr[Axis.SHOULDER], 0.0, places=9)
    self.assertAlmostEqual(j_bc[Axis.ELBOW] - j_pr[Axis.ELBOW], 2 * g_bc.length, places=9)


class ShoulderSnapAt180(unittest.TestCase):
  def test_negative_180_snaps_to_positive(self):
    """A pose pointing exactly along -y has shoulder = ±180; we snap to +180."""
    c = _config(wrist_offset=0, elbow_offset=0, elbow_zero_offset=0)
    g = GripperParams()
    pose = GripperLocation(
      location=Coordinate(x=0, y=-100, z=0), rotation=Rotation(z=180)
    )
    joints = kinematics.ik(pose, c, g)
    self.assertAlmostEqual(joints[Axis.SHOULDER], 180.0, places=9)


if __name__ == "__main__":
  unittest.main()
