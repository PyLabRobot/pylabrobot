import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3


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


if __name__ == "__main__":
  unittest.main()
