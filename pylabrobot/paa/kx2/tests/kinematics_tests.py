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

  def test_z_offset_sign_convention(self):
    """Pin the documented convention: positive z_offset = clamp sits below
    the wrist plate (in world frame, +Z up). FK with the same Z joint and
    a larger z_offset must produce a *lower* gripper z; IK must require a
    *higher* wrist Z to reach the same gripper z."""
    c = _config()
    joints = {Axis.SHOULDER: 0.0, Axis.Z: 100.0, Axis.ELBOW: 50.0, Axis.WRIST: 0.0}
    g_low = GripperParams(z_offset=0.0)
    g_high = GripperParams(z_offset=10.0)
    self.assertGreater(
      kinematics.fk(joints, c, g_low).location.z,
      kinematics.fk(joints, c, g_high).location.z,
      "FK: increasing z_offset should lower the gripper",
    )
    pose = GripperLocation(location=Coordinate(x=0, y=300, z=50), rotation=Rotation(z=0))
    self.assertGreater(
      kinematics.ik(pose, c, g_high)[Axis.Z],
      kinematics.ik(pose, c, g_low)[Axis.Z],
      "IK: increasing z_offset should raise the wrist target",
    )

  def test_ik_shoulder_branch_convention(self):
    """Pin shoulder = -degrees(atan2(x, y)) (CW from +Y, standard for KX2).

    The C# original computes this with four quadrant cases hand-rolling
    atan2 (KX2RobotControl.cs:7195-7268); Python uses the standard
    library `atan2`. The two are identical at every reachable pose modulo
    the -Y boundary, where Python snaps -180 -> +180 (kinematics.py:113)
    to match C#. This test pins one point per quadrant + every axis
    crossing so a future drive-by simplification can't silently flip a
    sign or drop the boundary snap.
    """
    c = _config(wrist_offset=0, elbow_offset=0, elbow_zero_offset=0)
    g = GripperParams()
    cases = [
      ("+Y axis",       0.0,  300.0,    0.0),
      ("Q1 (+x, +y)", 100.0,  100.0,  -45.0),
      ("+X axis",     300.0,    0.0,  -90.0),
      ("Q4 (+x, -y)", 100.0, -100.0, -135.0),
      ("-Y axis",       0.0, -300.0,  180.0),  # boundary: snapped from -180
      ("Q3 (-x, -y)", -100.0, -100.0, 135.0),
      ("-X axis",    -300.0,    0.0,   90.0),
      ("Q2 (-x, +y)", -100.0,  100.0,  45.0),
    ]
    for label, x, y, expected_shoulder in cases:
      pose = GripperLocation(
        location=Coordinate(x=x, y=y, z=0), rotation=Rotation(z=0),
      )
      joints = kinematics.ik(pose, c, g)
      self.assertAlmostEqual(
        joints[Axis.SHOULDER], expected_shoulder, places=9,
        msg=f"{label}: x={x}, y={y}",
      )


class FKIKAnchors(unittest.TestCase):
  """Anchor points pinning FK and IK output values for representative
  joints/poses. Values were snapshotted from the current implementation —
  if you change the FK or IK formula and these tests fail, the new
  output is provably different from the old one and needs explicit
  hardware verification."""

  def _cfg(self):
    def ax(min_travel=-180.0, max_travel=180.0, unlimited_travel=False):
      return AxisConfig(
        motor_conversion_factor=1.0, max_travel=max_travel, min_travel=min_travel,
        unlimited_travel=unlimited_travel, absolute_encoder=True,
        max_vel=100.0, max_accel=100.0,
        joint_move_direction=JointMoveDirection.Normal,
        digital_inputs={}, analog_inputs={}, outputs={},
      )
    return KX2Config(
      wrist_offset=10.0, elbow_offset=20.0, elbow_zero_offset=5.0,
      axes={
        Axis.SHOULDER: ax(),
        Axis.Z: ax(min_travel=0.0, max_travel=750.0),
        Axis.ELBOW: ax(min_travel=0.0, max_travel=300.0),
        Axis.WRIST: ax(unlimited_travel=True),
      },
      base_to_gripper_clearance_z=0.0, base_to_gripper_clearance_arm=0.0,
      robot_on_rail=False, servo_gripper=None,
    )

  def _gripper(self):
    return GripperParams(length=15.0, z_offset=3.0, finger_side="barcode_reader")

  def test_fk_zero_pose(self):
    """All joints at 0 → gripper at (0, 20, -3, 0°) for the default
    barcode-reader gripper (15 mm length, 3 mm z_offset)."""
    p = kinematics.fk(
      {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 0.0, Axis.WRIST: 0.0},
      self._cfg(), self._gripper(),
    )
    self.assertAlmostEqual(p.location.x, 0.0, places=6)
    self.assertAlmostEqual(p.location.y, 20.0, places=6)
    self.assertAlmostEqual(p.location.z, -3.0, places=6)
    self.assertAlmostEqual(p.rotation.z, 0.0, places=6)

  def test_fk_q1_mid(self):
    p = kinematics.fk(
      {Axis.SHOULDER: 30.0, Axis.Z: 100.0, Axis.ELBOW: 80.0, Axis.WRIST: 0.0},
      self._cfg(), self._gripper(),
    )
    self.assertAlmostEqual(p.location.x, -50.0, places=6)
    self.assertAlmostEqual(p.location.y, 86.6025, places=4)
    self.assertAlmostEqual(p.location.z, 97.0, places=6)
    self.assertAlmostEqual(p.rotation.z, 30.0, places=6)

  def test_fk_q2_mid(self):
    p = kinematics.fk(
      {Axis.SHOULDER: -45.0, Axis.Z: 50.0, Axis.ELBOW: 120.0, Axis.WRIST: 30.0},
      self._cfg(), self._gripper(),
    )
    self.assertAlmostEqual(p.location.x, 105.7193, places=4)
    self.assertAlmostEqual(p.location.y, 95.1127, places=4)
    self.assertAlmostEqual(p.location.z, 47.0, places=6)
    self.assertAlmostEqual(p.rotation.z, -15.0, places=6)

  def test_fk_max_extension_with_wrist(self):
    p = kinematics.fk(
      {Axis.SHOULDER: 90.0, Axis.Z: 200.0, Axis.ELBOW: 250.0, Axis.WRIST: -45.0},
      self._cfg(), self._gripper(),
    )
    self.assertAlmostEqual(p.location.x, -274.3934, places=4)
    self.assertAlmostEqual(p.location.y, -10.6066, places=4)
    self.assertAlmostEqual(p.location.z, 197.0, places=6)
    self.assertAlmostEqual(p.rotation.z, 45.0, places=6)

  def test_fk_wrist_180_keeps_yaw_in_range(self):
    """Wrist at 180° + shoulder at 0° gives yaw=180° (not -180° via the
    boundary snap)."""
    p = kinematics.fk(
      {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 100.0, Axis.WRIST: 180.0},
      self._cfg(), self._gripper(),
    )
    self.assertAlmostEqual(p.location.x, 0.0, places=6)
    self.assertAlmostEqual(p.location.y, 150.0, places=6)
    self.assertAlmostEqual(p.rotation.z, 180.0, places=6)

  def test_ik_on_plus_y_axis(self):
    """Target on +Y axis → shoulder = 0; elbow = y - sum_of_offsets."""
    pose = GripperLocation(location=Coordinate(x=0, y=300, z=50), rotation=Rotation(z=0))
    j = kinematics.ik(pose, self._cfg(), self._gripper())
    self.assertAlmostEqual(j[Axis.SHOULDER], 0.0, places=6)
    self.assertAlmostEqual(j[Axis.Z], 53.0, places=6)
    self.assertAlmostEqual(j[Axis.ELBOW], 280.0, places=4)
    self.assertAlmostEqual(j[Axis.WRIST], 0.0, places=6)

  def test_ik_q1_target(self):
    pose = GripperLocation(location=Coordinate(x=100, y=200, z=100), rotation=Rotation(z=30))
    j = kinematics.ik(pose, self._cfg(), self._gripper())
    self.assertAlmostEqual(j[Axis.SHOULDER], -23.474916, places=4)
    self.assertAlmostEqual(j[Axis.Z], 103.0, places=6)
    self.assertAlmostEqual(j[Axis.ELBOW], 197.209286, places=4)
    self.assertAlmostEqual(j[Axis.WRIST], 53.474916, places=4)

  def test_ik_q2_target(self):
    pose = GripperLocation(location=Coordinate(x=-150, y=150, z=75), rotation=Rotation(z=-45))
    j = kinematics.ik(pose, self._cfg(), self._gripper())
    self.assertAlmostEqual(j[Axis.SHOULDER], 40.955309, places=4)
    self.assertAlmostEqual(j[Axis.Z], 78.0, places=6)
    self.assertAlmostEqual(j[Axis.ELBOW], 177.661703, places=4)
    self.assertAlmostEqual(j[Axis.WRIST], -85.955309, places=4)

  def test_ik_negative_quadrant(self):
    """Target in negative-Y region — shoulder rotates past 90°."""
    pose = GripperLocation(location=Coordinate(x=-50, y=-200, z=120), rotation=Rotation(z=90))
    j = kinematics.ik(pose, self._cfg(), self._gripper())
    self.assertAlmostEqual(j[Axis.SHOULDER], 161.995838, places=4)
    self.assertAlmostEqual(j[Axis.Z], 123.0, places=6)
    self.assertAlmostEqual(j[Axis.ELBOW], 175.297408, places=4)
    self.assertAlmostEqual(j[Axis.WRIST], -71.995838, places=4)


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
