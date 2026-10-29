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
