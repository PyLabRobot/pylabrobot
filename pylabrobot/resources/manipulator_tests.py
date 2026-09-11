import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import Link
from pylabrobot.resources.resource import Resource
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3


class TestLink(unittest.TestCase):
  def test_a_child_link_turns_on_top_of_its_parent(self):
    base = Resource(name="base", size_x=500, size_y=500, size_z=0)
    first = Link(name="first", length=100.0)
    second = Link(name="second", length=50.0)
    base.assign_child_resource(first, location=Coordinate(0, 0, 0))
    first.assign_child_resource(second, location=Coordinate(first.get_size_x(), 0, 0))

    def far_end() -> Coordinate:
      carried = matrix_vector_multiply_3x3(
        second.get_absolute_rotation().get_rotation_matrix(),
        Coordinate(second.get_size_x(), 0, 0).vector(),
      )
      return second.get_absolute_location() + Coordinate(*carried)

    self.assertEqual(far_end(), Coordinate(150, 0, 0))

    second.rotate_to(z=90)
    self.assertEqual(far_end(), Coordinate(100, 50, 0))

    first.rotate_to(z=90)
    self.assertEqual(far_end(), Coordinate(-50, 100, 0))

  def test_a_link_comes_back_the_length_it_went_in(self):
    link = Link(name="link", length=137.7, model="demo")
    back = Link.deserialize(link.serialize())
    self.assertEqual(back.get_size_x(), 137.7)
    self.assertEqual(back.model, "demo")


if __name__ == "__main__":
  unittest.main()
