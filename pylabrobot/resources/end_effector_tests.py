import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.resource import Resource

# A gripper with every part a different size, so a part placed by the wrong measurement lands
# somewhere this notices.
LENGTH = 100.0
BODY = (50.0, 80.0, 20.0, -10.0, 0.0)
FINGER = (30.0, 6.0, 8.0, 60.0, 4.0)
PAD = (10.0, 4.0, 12.0, 85.0, -6.0)
JAW_RANGE = (20.0, 90.0)


def gripper(**overrides) -> MechanicalGripper:
  return MechanicalGripper(
    name="g", length=LENGTH, body=BODY, finger=FINGER, pad=PAD, jaw_range=JAW_RANGE, **overrides
  )


class TestTheSpan(unittest.TestCase):
  """A gripper is a link: it spans the joint it turns on to the point it grips at."""

  def test_the_grip_centre_is_the_far_joint(self):
    """A link's far joint is where the next link would go, and a gripper carries no next link, so
    what sits there is the point it is programmed against."""
    g = gripper()
    self.assertEqual(g.tool_center_point, g.far_joint)
    self.assertEqual(g.tool_center_point, Coordinate(100.0, 0.0, 0.0))


class TestJaws(unittest.TestCase):
  """How wide the jaws stand is state, not shape."""

  def test_a_width_stands_the_fingers_that_far_apart(self):
    """Measured centre to centre between the two fingers, symmetrically about the span, so a width
    applied to one finger only, or applied twice to one side, fails this."""
    g = gripper()
    for width in (90.0, 40.0, 20.0):
      g.jaw_width = width
      left, right = g.fingers
      centres = [
        cast(Coordinate, finger.location).y + finger.get_size_y() / 2 for finger in (left, right)
      ]
      self.assertAlmostEqual(centres[0] - centres[1], width)
      self.assertAlmostEqual(centres[0] + centres[1], 0.0)

  def test_the_jaws_refuse_a_width_they_do_not_reach(self):
    """At construction and afterwards alike: a model claiming a width the drive cannot reach would
    put the fingers where the arm cannot."""
    with self.assertRaises(ValueError):
      gripper(jaw_width=200.0)
    g = gripper()
    with self.assertRaises(ValueError):
      g.jaw_width = 5.0
    self.assertEqual(g.jaw_width, 90.0)

  def test_a_gripper_starts_open_unless_told_otherwise(self):
    """Open is the safe assumption for a model nothing has read yet, and a gripper known to home
    at a width is built at it instead."""
    self.assertEqual(gripper().jaw_width, 90.0)
    self.assertEqual(gripper(jaw_width=35.0).jaw_width, 35.0)


class TestPads(unittest.TestCase):
  """What actually touches the resource."""

  def test_a_pad_sits_the_same_way_on_both_fingers(self):
    """A pad is fixed to its finger, so it sits identically on each. Centring it across the finger
    the way material is centred across a link would put one pad inside the jaws and the other
    outside, since a finger's own origin is a corner rather than its middle - and a gripper whose
    two pads face opposite ways grips nothing where the model says it does."""
    g = gripper()
    left, right = (cast(Coordinate, cast(Resource, finger.pad).location) for finger in g.fingers)
    self.assertEqual(left, right)

    finger_thickness, pad_thickness = FINGER[1], PAD[1]
    self.assertGreaterEqual(left.y, 0.0)
    self.assertLessEqual(left.y + pad_thickness, finger_thickness)


if __name__ == "__main__":
  unittest.main()
