import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.resource import Resource

# A gripper with every part a different size, so a part placed by the wrong measurement lands
# somewhere this notices.
LENGTH = 100.0
BODY_X, BODY_Y, BODY_Z = 50.0, 80.0, 20.0
FINGER_X, FINGER_Y, FINGER_Z = 30.0, 6.0, 8.0
PAD_X, PAD_Y, PAD_Z = 10.0, 4.0, 12.0
BODY_LOCATION = Coordinate(-10.0, -40.0, 0.0)
FINGER_LOCATION = Coordinate(60.0, 0.0, 4.0)
PAD_LOCATION = Coordinate(25.0, 1.0, -10.0)


JAW_RANGE = (20.0, 90.0)


def gripper(**overrides) -> MechanicalGripper:
  return MechanicalGripper(
    name="g",
    length=LENGTH,
    body=Resource(name="g_body", size_x=BODY_X, size_y=BODY_Y, size_z=BODY_Z, category="body"),
    body_location=BODY_LOCATION,
    fingers=[
      Resource(
        name=f"g_finger_{side}",
        size_x=FINGER_X,
        size_y=FINGER_Y,
        size_z=FINGER_Z,
        category="finger",
      )
      for side in ("left", "right")
    ],
    finger_location=FINGER_LOCATION,
    pads=[
      Resource(
        name=f"g_finger_{side}_pad", size_x=PAD_X, size_y=PAD_Y, size_z=PAD_Z, category="pad"
      )
      for side in ("left", "right")
    ],
    pad_location=PAD_LOCATION,
    jaw_range=JAW_RANGE,
    **overrides,
  )


class TestTheSpan(unittest.TestCase):
  """A gripper is a link: it spans the joint it turns on to the point it grips at."""

  def test_the_grip_centre_sits_at_the_end_of_the_span(self):
    """A gripper spans the interface it is bolted to and the point it grips at, so its tool
    centre point is simply its length along that span. The fingers reach past it."""
    g = gripper()
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
    left, right = (cast(Coordinate, pad.location) for pad in g.pads)
    finger_thickness, pad_thickness = FINGER_Y, PAD_Y
    self.assertGreaterEqual(left.y, 0.0)
    self.assertLessEqual(left.y + pad_thickness, finger_thickness)


if __name__ == "__main__":
  unittest.main()
