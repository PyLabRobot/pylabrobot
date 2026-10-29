import unittest

from pylabrobot.brooks.precise_flex.resource_model import pf400_end_effector


def gripper(intelliguide: str):
  return pf400_end_effector.gripper("gripper", 162.0, (60.0, 145.0), intelliguide=intelliguide)


class TestPF400Gripper(unittest.TestCase):
  """The s23 and the v23: different bodies on the same jaws."""

  def test_the_jaws_stand_as_far_from_the_wrist_joint_on_either_body(self):
    v23, s23 = gripper("v23"), gripper("s23")
    for jaw, other in zip(v23.jaws, s23.jaws):
      here, there = jaw.get_location_wrt(v23), other.get_location_wrt(s23)
      self.assertAlmostEqual(v23.proximal_joint.x - here.x, s23.proximal_joint.x - there.x)
      self.assertAlmostEqual(v23.proximal_joint.z - here.z, s23.proximal_joint.z - there.z)

  def test_the_grip_centre_is_at_the_middle_of_the_fingers_height(self):
    for intelliguide in ("v23", "s23"):
      with self.subTest(intelliguide=intelliguide):
        hand = gripper(intelliguide)
        finger = hand.fingers[0]
        middle = finger.get_location_wrt(hand).z + finger.get_size_z() / 2
        self.assertAlmostEqual(middle, hand.proximal_joint.z + hand.tool_center_point.z)

  def test_the_fingers_stand_the_jaw_width_apart_about_the_wrist_joint(self):
    hand = gripper("v23")
    hand.jaw_width = 120.0
    left, right = (finger.get_location_wrt(hand).y for finger in hand.fingers)
    thickness = hand.fingers[1].get_size_y()
    self.assertAlmostEqual(left - (right + thickness), 120.0)
    self.assertAlmostEqual(left + right + thickness, 2 * hand.proximal_joint.y)
