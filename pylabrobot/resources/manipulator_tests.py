import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import Link, bolt_on
from pylabrobot.resources.resource import Resource
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3


class TestLink(unittest.TestCase):
  """A link is the span between two joints, and nothing else."""

  def test_a_chain_folds_on_its_joints(self):
    """Two links, the second placed on the first's far joint. Straight, the far end is both
    lengths out; folded square, the second link leaves the first's end sideways. Measured at the
    end of the chain rather than on either link, because that is the point a chain exists to
    place, and it is where an error in the joint offset would show."""
    base = Resource(name="base", size_x=500, size_y=500, size_z=0)
    first = Link(name="first", length=100.0)
    second = Link(name="second", length=50.0)
    base.assign_child_resource(first, location=Coordinate(0, 0, 0))
    first.assign_child_resource(second, location=first.far_joint)

    self.assertEqual(second.get_absolute_location() + second.far_joint, Coordinate(150, 0, 0))

    second.turn_to(90)
    # Through the link's own rotation rather than a vector worked out here, so the test exercises
    # the turn instead of restating its answer.
    carried = matrix_vector_multiply_3x3(
      second.get_absolute_rotation().get_rotation_matrix(), second.far_joint.vector()
    )
    end = second.get_absolute_location() + Coordinate(*carried)
    self.assertEqual(end, Coordinate(100, 50, 0))

  def test_turning_to_an_angle_is_absolute(self):
    """`turn_to` points the link somewhere, where `rotate` turns it by an amount. A drive commanded
    to the same angle twice has not moved twice, and the model has to say the same."""
    base = Resource(name="base", size_x=500, size_y=500, size_z=0)
    link = Link(name="link", length=100.0)
    base.assign_child_resource(link, location=Coordinate(0, 0, 0))

    link.turn_to(30)
    link.turn_to(30)
    self.assertEqual(link.rotation.z, 30)

    link.rotate(z=30)
    self.assertEqual(link.rotation.z, 60)

  def test_a_link_can_be_given_the_joint_it_turns_on(self):
    """The joint is where the link is placed, so naming one places it. A link that has never been
    placed and is given none has nothing to turn on, and says so rather than turning about the
    origin."""
    base = Resource(name="base", size_x=500, size_y=500, size_z=0)
    link = Link(name="link", length=100.0)
    base.assign_child_resource(link, location=Coordinate(0, 0, 0))

    link.turn_to(0, about=Coordinate(10, 20, 30))
    self.assertEqual(link.location, Coordinate(10, 20, 30))

    with self.assertRaises(RuntimeError):
      Link(name="loose", length=100.0).turn_to(0)


class TestBoltOn(unittest.TestCase):
  """Material hung on a link, which carries none of its own."""

  def test_a_part_is_centred_across_the_link_and_stands_where_it_says(self):
    """A link is a line through its joints, so material is centred across it in Y and offset along
    and above it by what the part states. Its category is what the part is, and its model is named
    after the link's, so two of the same part on one link are one model mounted twice."""
    link = Link(name="link", length=100.0, model="a_link")
    part = bolt_on(link, "shell", (40.0, 12.0, 5.0, 7.0, 3.0))

    self.assertEqual(part.location, Coordinate(7.0, -6.0, 3.0))
    self.assertEqual(
      (part.name, part.category, part.model), ("link_shell", "shell", "a_link_shell")
    )

  def test_a_link_with_no_model_gives_its_parts_none(self):
    """There is nothing to name them after, and a part carrying a model nothing describes is worse
    than one carrying none."""
    part = bolt_on(Link(name="link", length=100.0), "shell", (40.0, 12.0, 5.0, 0.0, 0.0))
    self.assertIsNone(part.model)


if __name__ == "__main__":
  unittest.main()
