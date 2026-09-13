import math
import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.resource import Resource

# Measured off a Hamilton iSWAP. The origin is the body's corner, and the joint sits inside it.
LENGTH = 137.7
BODY_SIZE = (59.0, 90.0, 20.3)
# The joint deliberately sits off the body's own middle, 45.0, so that a test cannot pass by
# reading one when it means the other.
PROXIMAL_JOINT = Coordinate(13.0, 38.0, 1.3)
BODY_LOCATION = Coordinate(0.0, 0.0, 0.0)
FINGER_LOCATION = Coordinate(19.5, 38.0, 5.3)
PAD_LOCATION = Coordinate(109.0, 1.5, -17.0)
JAW_RANGE = (70.844, 133.706)


def tcp(z: float = 0.0, y: float = 0.0) -> Coordinate:
  """The grip centre `LENGTH` along the span from the joint, and `y`/`z` off it."""
  return Coordinate(PROXIMAL_JOINT.x + LENGTH, PROXIMAL_JOINT.y + y, PROXIMAL_JOINT.z + z)


def gripper(**overrides) -> MechanicalGripper:
  body = Resource(
    name="demo_body",
    size_x=BODY_SIZE[0],
    size_y=BODY_SIZE[1],
    size_z=BODY_SIZE[2],
    category="body",
  )

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
    proximal_joint=PROXIMAL_JOINT,
    tool_center_point=tcp(),
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
    self.assertEqual(g.tool_center_point, tcp())
    self.assertAlmostEqual(g.length, LENGTH)

  def test_a_tool_can_grip_below_where_it_is_mounted(self):
    g = gripper(tool_center_point=tcp(z=-13.0))
    self.assertEqual(g.tool_center_point, tcp(z=-13.0))
    # The span now runs diagonally, so the link is longer than its reach along X.
    self.assertAlmostEqual(g.length, math.dist((LENGTH, -13.0), (0.0, 0.0)))

  def test_nothing_attaches_past_a_tool(self):
    self.assertIsNone(gripper().distal_joint)

  def test_the_member_is_sized_to_its_body_not_to_its_fingers(self):
    g = gripper()
    self.assertEqual((g.get_size_x(), g.get_size_y(), g.get_size_z()), BODY_SIZE)
    # The body states that box once: the member is not told its own size a second time.
    self.assertEqual(
      (g.body.get_size_x(), g.body.get_size_y(), g.body.get_size_z()),
      (g.get_size_x(), g.get_size_y(), g.get_size_z()),
    )
    # The fingers are longer than the body they hang from, and reach past it.
    self.assertGreater(g.fingers[0].get_size_x(), g.get_size_x())

  def test_nothing_past_a_tool_means_no_joint_in_its_payload(self):
    self.assertNotIn("distal_joint", gripper().serialize())


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
      # They straddle the grip centre, which is neither the member's middle nor its origin.
      self.assertAlmostEqual(faces[0] + faces[1], 2 * tcp().y)
      self.assertNotAlmostEqual(tcp().y, g.get_size_y() / 2.0)

  def test_the_jaws_refuse_a_width_they_do_not_reach(self):
    with self.assertRaises(ValueError):
      gripper(jaw_width=200.0)
    g = gripper()
    before = g.jaw_width
    with self.assertRaises(ValueError):
      g.jaw_width = 5.0
    self.assertEqual(g.jaw_width, before)

  def test_the_jaws_close_on_the_grip_centre_not_on_the_joint(self):
    """A tool that grips off to one side stands its fingers there, not over its own mounting."""
    g = gripper(tool_center_point=tcp(y=-18.0))
    left, right = g.fingers
    faces = [
      cast(Coordinate, left.location).y,
      cast(Coordinate, right.location).y + right.get_size_y(),
    ]
    self.assertAlmostEqual(faces[0] + faces[1], 2 * (PROXIMAL_JOINT.y - 18.0))
    self.assertAlmostEqual(faces[0] - faces[1], g.jaw_width)

  def test_the_jaws_keep_the_reach_and_height_they_were_given(self):
    """`jaw_width` moves the fingers across the span and must leave X and Z alone."""
    g = gripper()
    for width in (133.706, 100.0, 70.844):
      g.jaw_width = width
      for finger in g.fingers:
        here = cast(Coordinate, finger.location)
        self.assertAlmostEqual(here.x, FINGER_LOCATION.x)
        self.assertAlmostEqual(here.z, FINGER_LOCATION.z)

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
    g = gripper(jaw_width=100.0, tool_center_point=tcp(z=-13.0))
    g.metadata = {"serial": "X1"}
    g.preferred_pickup_location = Coordinate(1.0, 2.0, 3.0)
    back = MechanicalGripper.deserialize(g.serialize())
    back.load_all_state(g.serialize_all_state())

    self.assertEqual(back.metadata, {"serial": "X1"})
    self.assertEqual(back.preferred_pickup_location, Coordinate(1.0, 2.0, 3.0))
    self.assertEqual(back.tool_center_point, g.tool_center_point)
    self.assertEqual(back.proximal_joint, PROXIMAL_JOINT)
    self.assertEqual((back.get_size_x(), back.get_size_y(), back.get_size_z()), BODY_SIZE)
    self.assertEqual(back.jaw_range, g.jaw_range)
    self.assertEqual(back.jaw_width, 100.0)
    self.assertEqual(cast(Coordinate, back.body.location), BODY_LOCATION)
    self.assertEqual([pad.location for pad in back.pads], [PAD_LOCATION] * 2)
    self.assertEqual([pad.parent for pad in back.pads], back.fingers)

  def test_what_a_finger_carries_besides_its_pad_comes_back_on_that_finger(self):
    g = gripper()
    sensor = Resource(name="demo_sensor", size_x=5.0, size_y=5.0, size_z=5.0)
    g.fingers[0].assign_child_resource(sensor, location=Coordinate(80.0, 0.0, 2.0))
    back = MechanicalGripper.deserialize(g.serialize())

    self.assertEqual([pad.name for pad in back.pads], [pad.name for pad in g.pads])
    self.assertEqual([pad.parent for pad in back.pads], back.fingers)
    carried = back.get_resource("demo_sensor")
    self.assertIs(carried.parent, back.fingers[0])
    self.assertEqual(carried.location, Coordinate(80.0, 0.0, 2.0))
    # Through `copy`, which `rotated` goes through too.
    copied = g.copy()
    self.assertIs(copied.get_resource("demo_sensor").parent, copied.fingers[0])

  def test_what_a_bare_finger_carries_is_not_taken_for_a_pad(self):
    g = gripper(pads=None, pad_location=None)
    for finger in g.fingers:
      finger.assign_child_resource(
        Resource(name=f"{finger.name}_sensor", size_x=5.0, size_y=5.0, size_z=5.0),
        location=Coordinate(80.0, 0.0, 2.0),
      )
    back = MechanicalGripper.deserialize(g.serialize())

    self.assertEqual(back.pads, [])
    self.assertEqual(
      [[child.name for child in finger.children] for finger in back.fingers],
      [[f"{finger.name}_sensor"] for finger in g.fingers],
    )

  def test_a_pad_moved_since_it_was_fitted_comes_back_where_it_was_moved(self):
    g = gripper()
    g.pads[1].location = PAD_LOCATION + Coordinate(0.0, 0.0, 2.0)
    back = MechanicalGripper.deserialize(g.serialize())
    self.assertEqual(
      [pad.location for pad in back.pads], [PAD_LOCATION, PAD_LOCATION + Coordinate(0.0, 0.0, 2.0)]
    )

  def test_parts_are_found_by_name_not_by_where_they_sit_among_the_children(self):
    g = gripper()
    held = Resource(name="demo_plate", size_x=127.76, size_y=85.48, size_z=14.2)
    g.assign_child_resource(held, location=Coordinate(90.0, -4.7, -20.0))
    data = g.serialize()
    data["children"].reverse()
    back = MechanicalGripper.deserialize(data)

    self.assertEqual(back.body.name, "demo_body")
    self.assertEqual([finger.name for finger in back.fingers], [f.name for f in g.fingers])
    plate = back.get_resource("demo_plate")
    self.assertIs(plate.parent, back)
    self.assertEqual(plate.location, Coordinate(90.0, -4.7, -20.0))

  def test_a_pad_missing_from_its_finger_is_refused(self):
    data = gripper().serialize()
    finger = next(child for child in data["children"] if child["name"] == "demo_finger_right")
    finger["children"] = []
    with self.assertRaises(ValueError):
      MechanicalGripper.deserialize(data)


if __name__ == "__main__":
  unittest.main()
