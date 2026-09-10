import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.resource import Resource

# A gripper that exists: every measurement here is a Hamilton iSWAP's, so the fixture is a
# shape that could be built rather than one chosen to make the arithmetic easy. No two of the
# nine dimensions are equal, so a part placed by the wrong measurement lands where these notice.
LENGTH = 137.7
BODY_LOCATION = Coordinate(-13.0, -45.0, -1.3)
FINGER_LOCATION = Coordinate(6.5, 0.0, 4.0)
PAD_LOCATION = Coordinate(109.0, 1.5, -17.0)
# What the gripper drive's own travel comes to, closed and open.
JAW_RANGE = (70.844, 133.706)


def gripper(**overrides) -> MechanicalGripper:
  body = Resource(name="demo_body", size_x=59.0, size_y=90.0, size_z=20.3, category="body")

  fingers = [
    Resource(
      name=f"demo_finger_{side}",
      size_x=135.0,
      size_y=7.0,
      size_z=8.0,
      category="finger",
    )
    for side in ("left", "right")
  ]
  pads = [
    Resource(name=f"demo_finger_{side}_pad", size_x=37.0, size_y=4.0, size_z=17.0, category="pad")
    for side in ("left", "right")
  ]

  return MechanicalGripper(
    name="demo_gripper",
    length=LENGTH,
    body=body,
    body_location=BODY_LOCATION,
    fingers=fingers,
    finger_location=FINGER_LOCATION,
    pads=pads,
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
    self.assertEqual(g.tool_center_point, Coordinate(LENGTH, 0.0, 0.0))


class TestJaws(unittest.TestCase):
  """How wide the jaws stand is state, not shape."""

  def test_a_width_stands_the_fingers_that_far_apart(self):
    """Measured centre to centre between the two fingers, symmetrically about the span, so a width
    applied to one finger only, or applied twice to one side, fails this."""
    g = gripper()
    for width in (133.706, 100.0, 70.844):
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
    self.assertEqual(g.jaw_width, JAW_RANGE[1])

  def test_a_gripper_starts_open_unless_told_otherwise(self):
    """Open is the safe assumption for a model nothing has read yet, and a gripper known to home
    at a width is built at it instead."""
    self.assertEqual(gripper().jaw_width, JAW_RANGE[1])
    self.assertEqual(gripper(jaw_width=100.0).jaw_width, 100.0)


class TestPads(unittest.TestCase):
  """What actually touches the resource."""

  def test_a_pad_sits_the_same_way_on_both_fingers(self):
    """A pad is fixed to its finger, so it sits identically on each. Centring it across the finger
    the way material is centred across a link would put one pad inside the jaws and the other
    outside, since a finger's own origin is a corner rather than its middle - and a gripper whose
    two pads face opposite ways grips nothing where the model says it does."""
    g = gripper()
    for jaw, face in zip(g.fingers, g.pads):
      sits_at = cast(Coordinate, face.location).y
      self.assertGreaterEqual(sits_at, 0.0)
      self.assertLessEqual(sits_at + face.get_size_y(), jaw.get_size_y())


if __name__ == "__main__":
  unittest.main()
