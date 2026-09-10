import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.resource import Resource

# Measured off a Hamilton iSWAP.
LENGTH = 137.7
BODY_LOCATION = Coordinate(-13.0, -45.0, -1.3)
FINGER_LOCATION = Coordinate(6.5, 0.0, 4.0)
PAD_LOCATION = Coordinate(109.0, 1.5, -17.0)
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

  arguments = dict(
    name="demo_gripper",
    length=LENGTH,
    body=body,
    body_location=BODY_LOCATION,
    fingers=fingers,
    finger_location=FINGER_LOCATION,
    jaw_range=JAW_RANGE,
    pads=pads,
    pad_location=PAD_LOCATION,
  )
  return MechanicalGripper(**{**arguments, **overrides})


class TestTheSpan(unittest.TestCase):
  def test_the_grip_centre_sits_at_the_end_of_the_span(self):
    g = gripper()
    self.assertEqual(g.tool_center_point, Coordinate(LENGTH, 0.0, 0.0))


class TestJaws(unittest.TestCase):
  def test_a_width_stands_the_fingers_that_far_apart(self):
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
    with self.assertRaises(ValueError):
      gripper(jaw_width=200.0)
    g = gripper()
    before = g.jaw_width
    with self.assertRaises(ValueError):
      g.jaw_width = 5.0
    self.assertEqual(g.jaw_width, before)

  def test_a_gripper_starts_open_unless_told_otherwise(self):
    self.assertEqual(gripper().jaw_width, JAW_RANGE[1])
    self.assertEqual(gripper(jaw_width=100.0).jaw_width, 100.0)


class TestPads(unittest.TestCase):
  def test_a_gripper_can_have_bare_fingers(self):
    g = gripper(pads=None, pad_location=None)
    self.assertEqual(g.pads, [])
    self.assertEqual([jaw.children for jaw in g.fingers], [[], []])

  def test_pads_and_their_location_go_together(self):
    with self.assertRaises(ValueError):
      gripper(pad_location=None)
    with self.assertRaises(ValueError):
      gripper(pads=None)

  def test_a_pad_sits_inside_its_finger(self):
    g = gripper()
    for jaw, face in zip(g.fingers, g.pads):
      sits_at = cast(Coordinate, face.location).y
      self.assertGreaterEqual(sits_at, 0.0)
      self.assertLessEqual(sits_at + face.get_size_y(), jaw.get_size_y())


if __name__ == "__main__":
  unittest.main()
