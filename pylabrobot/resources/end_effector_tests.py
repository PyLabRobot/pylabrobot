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
    self.assertEqual(gripper().tool_center_point, Coordinate(LENGTH, 0.0, 0.0))

  def test_a_tool_can_grip_below_where_it_is_mounted(self):
    g = gripper(tool_center_point_z=-13.0)
    self.assertEqual(g.tool_center_point, Coordinate(LENGTH, 0.0, -13.0))


class TestJaws(unittest.TestCase):
  def test_a_width_is_the_gap_the_fingers_leave_between_them(self):
    g = gripper()
    for width in (133.706, 100.0, 70.844):
      g.jaw_width = width
      left, right = g.fingers
      faces = [
        cast(Coordinate, left.location).y,
        cast(Coordinate, right.location).y + right.get_size_y(),
      ]
      self.assertAlmostEqual(faces[0] - faces[1], width)
      self.assertAlmostEqual(faces[0] + faces[1], 0.0)

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
    bare = gripper(pads=None, pad_location=None)
    self.assertEqual(bare.pads, [])
    self.assertEqual([jaw.children for jaw in bare.fingers], [[], []])

    padded = gripper()
    self.assertEqual(len(padded.pads), 2)
    self.assertEqual([jaw.children for jaw in padded.fingers], [[pad] for pad in padded.pads])

  def test_pads_and_their_location_go_together(self):
    with self.assertRaises(ValueError):
      gripper(pad_location=None)
    with self.assertRaises(ValueError):
      gripper(pads=None)

  def test_a_pad_is_fixed_to_its_own_finger_where_it_was_put(self):
    g = gripper()
    for jaw, face in zip(g.fingers, g.pads):
      self.assertIs(face.parent, jaw)
      self.assertEqual(face.location, PAD_LOCATION)


class TestRoundTrip(unittest.TestCase):
  def test_a_gripper_comes_back_with_its_parts_and_its_width(self):
    g = gripper(jaw_width=100.0, tool_center_point_z=-13.0)
    back = MechanicalGripper.deserialize(g.serialize())
    back.load_all_state(g.serialize_all_state())

    self.assertEqual(back.tool_center_point, g.tool_center_point)
    self.assertEqual(back.jaw_range, g.jaw_range)
    self.assertEqual(back.jaw_width, 100.0)
    self.assertEqual(cast(Coordinate, back.body.location), BODY_LOCATION)
    self.assertEqual([pad.location for pad in back.pads], [PAD_LOCATION] * 2)
    self.assertEqual([pad.parent for pad in back.pads], back.fingers)


if __name__ == "__main__":
  unittest.main()
