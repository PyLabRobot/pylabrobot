import math
import unittest

from pylabrobot.brooks.precise_flex import kinematics


class TestClassifyPF400Reach(unittest.TestCase):
  """Link lengths are classified as standard, extended, or unknown reach."""

  def test_classify_pf400_reach(self):
    self.assertEqual(kinematics._classify_pf400_reach((225, 210)), "standard")
    self.assertEqual(kinematics._classify_pf400_reach((302, 289)), "extended")
    self.assertEqual(kinematics._classify_pf400_reach((303, 288)), "extended")  # within tolerance
    self.assertEqual(kinematics._classify_pf400_reach((500, 500)), "unknown")


class TestOutlineClearance(unittest.TestCase):
  """How far apart two convex outlines stand, and how deep they overlap."""

  def test_apart_overlapping_and_never_more_than_the_true_distance(self):
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]

    def moved(x: float, y: float):
      return [(point_x + x, point_y + y) for point_x, point_y in square]

    # A diamond's tip at the square's face: only the square's edge sees the 5 mm gap.
    diamond = [(20.0, 0.0), (25.0, 5.0), (20.0, 10.0), (15.0, 5.0)]
    for outline, other, clearance in (
      (square, moved(13.0, 0.0), 3.0),
      (moved(0.0, -17.0), square, 7.0),
      (square, moved(8.0, 1.0), -2.0),
      # Corner to corner they are 5 mm apart; the widest gap an edge leaves is 4 mm.
      (square, moved(14.0, 13.0), 4.0),
      (diamond, square, 5.0),
    ):
      with self.subTest(other=other):
        self.assertAlmostEqual(kinematics.compute_outline_clearance(outline, other), clearance)


class TestElbowAndWrist(unittest.TestCase):
  """Where the two joints stand: the one place the links' trigonometry is written."""

  def test_stretched_out_folded_back_and_turned(self):
    p = kinematics.PF400Params()  # links 302 and 289
    for shoulder, elbow, want_elbow, want_wrist in (
      (0.0, 0.0, (302.0, 0.0), (591.0, 0.0)),
      (0.0, 180.0, (302.0, 0.0), (13.0, 0.0)),
      (90.0, 0.0, (0.0, 302.0), (0.0, 591.0)),
      (0.0, 90.0, (302.0, 0.0), (302.0, 289.0)),
    ):
      got_elbow, got_wrist = kinematics.compute_elbow_and_wrist(
        p, math.radians(shoulder), math.radians(elbow)
      )
      for got, want in ((got_elbow, want_elbow), (got_wrist, want_wrist)):
        self.assertAlmostEqual(got[0], want[0])
        self.assertAlmostEqual(got[1], want[1])

  def test_the_gripper_pose_is_the_wrist_joint_and_the_tool_beyond_it(self):
    p = kinematics.PF400Params(gripper_length=162.0)
    joints = {
      kinematics.PreciseFlexAxis.BASE: 100.0,
      kinematics.PreciseFlexAxis.SHOULDER: 30.0,
      kinematics.PreciseFlexAxis.ELBOW: 70.0,
      kinematics.PreciseFlexAxis.WRIST: -40.0,
    }
    _, wrist = kinematics.compute_elbow_and_wrist(p, math.radians(30.0), math.radians(70.0))
    pose = kinematics.fk(joints, p)
    yaw = math.radians(30.0 + 70.0 - 40.0)
    # A coordinate keeps four decimal places.
    self.assertAlmostEqual(pose.location.x, wrist[0] + 162.0 * math.cos(yaw), places=3)
    self.assertAlmostEqual(pose.location.y, wrist[1] + 162.0 * math.sin(yaw), places=3)
