import unittest
from typing import Tuple, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector_tests import gripper as demo_gripper
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3


def absolute(resource: Resource, point: Coordinate) -> Coordinate:
  """Where `point` in `resource`'s own frame sits on the deck, in mm."""
  carried = matrix_vector_multiply_3x3(
    resource.get_absolute_rotation().get_rotation_matrix(), point.vector()
  )
  return resource.get_absolute_location() + Coordinate(*carried)


def straight_member(name: str, length: float, inset: Coordinate) -> LinkBody:
  """A member whose joints sit `inset` in from its own origin, `length` apart along X."""
  return LinkBody(
    name=name,
    size_x=length + 2 * inset.x,
    size_y=2 * inset.y,
    size_z=2 * inset.z if inset.z else 1.0,
    proximal_joint=inset,
    distal_joint=Coordinate(inset.x + length, inset.y, inset.z),
  )


class TestLinkBody(unittest.TestCase):
  def test_the_link_is_the_distance_between_the_joints(self):
    member = straight_member("member", length=100.0, inset=Coordinate(12.7, 12.75, 0.0))
    self.assertAlmostEqual(cast(float, member.length), 100.0)
    # The member is longer than the link it carries: material overhangs both joints.
    self.assertEqual(member.get_size_x(), 125.4)

  def test_a_link_runs_to_wherever_the_distal_joint_is(self):
    member = LinkBody(
      name="bent",
      size_x=100.0,
      size_y=100.0,
      size_z=10.0,
      proximal_joint=Coordinate(0.0, 0.0, 0.0),
      distal_joint=Coordinate(30.0, 40.0, 0.0),
    )
    self.assertAlmostEqual(cast(float, member.length), 50.0)

  def test_a_member_that_ends_the_chain_has_no_link(self):
    member = LinkBody(
      name="last",
      size_x=10.0,
      size_y=10.0,
      size_z=10.0,
      proximal_joint=Coordinate(5.0, 5.0, 0.0),
    )
    self.assertIsNone(member.distal_joint)
    self.assertIsNone(member.length)

  def test_a_child_link_turns_on_top_of_its_parent(self):
    inset = Coordinate(10.0, 5.0, 0.0)
    base = Resource(name="base", size_x=500, size_y=500, size_z=0)
    first = straight_member("first", length=100.0, inset=inset)
    second = straight_member("second", length=50.0, inset=inset)
    base.assign_child_resource(first, location=Coordinate(-inset.x, -inset.y, 0))
    # The second member's joint lands on the first member's far joint.
    first.assign_child_resource(
      second, location=Coordinate(cast(Coordinate, first.distal_joint).x - inset.x, 0, 0)
    )

    def far_end() -> Coordinate:
      carried = matrix_vector_multiply_3x3(
        second.get_absolute_rotation().get_rotation_matrix(),
        cast(Coordinate, second.distal_joint).vector(),
      )
      return second.get_absolute_location() + Coordinate(*carried)

    self.assertEqual(far_end(), Coordinate(150, 0, 0))

    # A member turns on its own joint, which is not its origin, so the pivot is not optional.
    second.rotate_to(z=90, pivot_coordinate=second.proximal_joint)
    self.assertEqual(far_end(), Coordinate(100, 50, 0))

    first.rotate_to(z=90, pivot_coordinate=first.proximal_joint)
    self.assertEqual(far_end(), Coordinate(-50, 100, 0))

  def test_a_member_comes_back_with_its_shape_and_both_joints(self):
    member = LinkBody(
      name="member",
      size_x=163.4,
      size_y=25.5,
      size_z=15.3,
      proximal_joint=Coordinate(12.7, 12.75, -20.3),
      distal_joint=Coordinate(150.4, 12.75, -20.3),
      model="demo",
    )
    back = LinkBody.deserialize(member.serialize())

    self.assertEqual((back.get_size_x(), back.get_size_y(), back.get_size_z()), (163.4, 25.5, 15.3))
    self.assertEqual(back.proximal_joint, member.proximal_joint)
    self.assertEqual(back.distal_joint, member.distal_joint)
    self.assertAlmostEqual(cast(float, back.length), cast(float, member.length))
    self.assertEqual(back.model, "demo")

  def test_a_member_that_ends_the_chain_comes_back_without_one(self):
    member = LinkBody(
      name="last",
      size_x=10.0,
      size_y=10.0,
      size_z=10.0,
      proximal_joint=Coordinate(5.0, 5.0, 0.0),
    )
    back = LinkBody.deserialize(member.serialize())
    self.assertIsNone(back.distal_joint)


class TestAnAssembledArm(unittest.TestCase):
  """A member and the tool it carries, placed on a deck and turned joint by joint.

  The two are built in separate modules and every other test exercises them apart, so this is
  the only place the whole chain's absolute geometry is pinned.
  """

  def arm(self):
    deck = Resource("deck", size_x=1000, size_y=1000, size_z=10)
    deck.location = Coordinate.zero()
    forearm = LinkBody(
      name="forearm",
      size_x=220.0,
      size_y=40.0,
      size_z=20.0,
      proximal_joint=Coordinate(10.0, 20.0, 10.0),
      distal_joint=Coordinate(210.0, 20.0, 10.0),
    )
    deck.assign_child_resource(forearm, location=Coordinate(100.0, 100.0, 0.0))

    hand = demo_gripper()
    # The tool's own joint lands on the member's far joint. That is what mounting means.
    forearm.assign_child_resource(
      hand, location=cast(Coordinate, forearm.distal_joint) - hand.proximal_joint
    )
    return forearm, hand

  def wrist(self, forearm: LinkBody, hand) -> Tuple[Coordinate, Coordinate]:
    """Where the wrist is, read off each side of the joint independently."""
    return (
      absolute(forearm, cast(Coordinate, forearm.distal_joint)),
      absolute(hand, hand.proximal_joint),
    )

  def test_the_tool_hangs_where_the_member_ends(self):
    forearm, hand = self.arm()
    from_member, from_tool = self.wrist(forearm, hand)
    self.assertEqual(from_member, Coordinate(310, 120, 10))
    self.assertEqual(from_member, from_tool)
    self.assertEqual(absolute(hand, hand.tool_center_point), Coordinate(447.7, 120, 10))

  def test_the_tool_rides_the_member_it_is_mounted_on(self):
    forearm, hand = self.arm()
    forearm.rotate_to(z=90, pivot_coordinate=forearm.proximal_joint)

    from_member, from_tool = self.wrist(forearm, hand)
    self.assertEqual(from_member, Coordinate(110, 320, 10))
    self.assertEqual(from_member, from_tool)
    # The tool did not turn on its own joint, so it swung round with the member carrying it.
    self.assertEqual(absolute(hand, hand.tool_center_point), Coordinate(110, 457.7, 10))

  def test_the_tool_also_turns_on_its_own_joint(self):
    forearm, hand = self.arm()
    forearm.rotate_to(z=90, pivot_coordinate=forearm.proximal_joint)
    hand.rotate_to(z=90, pivot_coordinate=hand.proximal_joint)

    from_member, from_tool = self.wrist(forearm, hand)
    # The wrist is the fixed point of the tool's own turn, so it has not moved.
    self.assertEqual(from_member, Coordinate(110, 320, 10))
    self.assertEqual(from_member, from_tool)
    # Two right angles, so the grip centre now points back the way the member came.
    self.assertEqual(absolute(hand, hand.tool_center_point), Coordinate(-27.7, 320, 10))

  def test_the_fingers_travel_with_the_tool(self):
    forearm, hand = self.arm()
    at_rest = [absolute(finger, Coordinate.zero()) for finger in hand.fingers]
    self.assertEqual(at_rest[0], Coordinate(316.5, 186.853, 14))
    self.assertEqual(at_rest[1], Coordinate(316.5, 46.147, 14))

    forearm.rotate_to(z=90, pivot_coordinate=forearm.proximal_joint)
    turned = [absolute(finger, Coordinate.zero()) for finger in hand.fingers]
    self.assertEqual(turned[0], Coordinate(43.147, 326.5, 14))
    self.assertEqual(turned[1], Coordinate(183.853, 326.5, 14))


if __name__ == "__main__":
  unittest.main()
