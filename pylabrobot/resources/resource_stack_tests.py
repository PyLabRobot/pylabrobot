"""Tests for ResourceStack resource"""

import unittest

from pylabrobot.resources import Coordinate, Lid, Plate, Resource

from .resource_stack import ResourceStack


class ResourceStackTests(unittest.TestCase):
  def test_create(self):
    stack = ResourceStack(
      "stack",
      "z",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(len(stack.children), 2)

  def test_create_x(self):
    stack = ResourceStack(
      "stack",
      "x",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 0))
    self.assertEqual(stack.get_resource("B").location, Coordinate(10, 0, 0))

    self.assertEqual(stack.get_absolute_size_x(), 20)
    self.assertEqual(stack.get_absolute_size_y(), 10)
    self.assertEqual(stack.get_absolute_size_z(), 10)

  def test_create_y(self):
    stack = ResourceStack(
      "stack",
      "y",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 0))
    self.assertEqual(stack.get_resource("B").location, Coordinate(0, 10, 0))

    self.assertEqual(stack.get_absolute_size_x(), 10)
    self.assertEqual(stack.get_absolute_size_y(), 20)
    self.assertEqual(stack.get_absolute_size_z(), 10)

  def test_create_z(self):
    stack = ResourceStack(
      "stack",
      "z",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 10))
    self.assertEqual(stack.get_resource("B").location, Coordinate(0, 0, 0))

    self.assertEqual(stack.get_absolute_size_x(), 10)
    self.assertEqual(stack.get_absolute_size_y(), 10)
    self.assertEqual(stack.get_absolute_size_z(), 20)

  def test_get_size_empty_stack(self):
    stack = ResourceStack("stack", "z")
    self.assertEqual(stack.get_absolute_size_x(), 0)
    self.assertEqual(stack.get_absolute_size_y(), 0)
    self.assertEqual(stack.get_absolute_size_z(), 0)

  # Tests for using ResourceStack as a stacking area, like the one near the washer on the STARs.

  def test_add_item(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.assign_child_resource(plate)
    self.assertEqual(stacking_area.get_top_item(), plate)

  def test_get_absolute_location_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.location = Coordinate.zero()
    stacking_area.assign_child_resource(plate)
    self.assertEqual(plate.get_absolute_location(), Coordinate(0, 0, 0))

  def test_get_absolute_location_lid(self):
    lid = Lid(name="lid", size_x=1, size_y=1, size_z=1, nesting_z_height=0)
    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.location = Coordinate.zero()
    stacking_area.assign_child_resource(lid)
    self.assertEqual(
      stacking_area.get_top_item().get_absolute_location(),
      Coordinate(0, 0, 0),
    )

  def test_get_absolute_location_stack_height(self):
    lid = Lid(name="lid", size_x=1, size_y=1, size_z=1, nesting_z_height=0)
    lid2 = Lid(name="lid2", size_x=1, size_y=1, size_z=1, nesting_z_height=0)

    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.location = Coordinate.zero()
    stacking_area.assign_child_resource(lid)
    top_item = stacking_area.get_top_item()
    assert top_item is not None
    self.assertEqual(top_item.get_absolute_location(), Coordinate(0, 0, 0))

    stacking_area.assign_child_resource(lid2)
    top_item = stacking_area.get_top_item()
    assert top_item is not None
    self.assertEqual(top_item.get_absolute_location(), Coordinate(0, 0, 1))


class ResourceStackPlateNestingTests(unittest.TestCase):
  """Bare plates with a known `stacking_z_height` should nest into one another in a z-stack."""

  def _plate(self, name, stacking_z_height=None):
    return Plate(
      name,
      size_x=10,
      size_y=10,
      size_z=10,
      ordered_items={},
      stacking_z_height=stacking_z_height,
    )

  def test_without_stacking_z_height_no_nesting(self):
    # backwards-compatible: plates without a stacking pitch stack at full size_z.
    stack = ResourceStack("s", "z")
    stack.location = Coordinate.zero()
    stack.assign_child_resource(self._plate("p1"))
    stack.assign_child_resource(self._plate("p2"))
    self.assertEqual(stack.get_size_z(), 20)
    self.assertEqual(stack.get_top_item().get_absolute_location(), Coordinate(0, 0, 10))

  def test_two_plates_nest(self):
    stack = ResourceStack("s", "z")
    stack.location = Coordinate.zero()
    stack.assign_child_resource(self._plate("p1", stacking_z_height=4))
    stack.assign_child_resource(self._plate("p2", stacking_z_height=4))
    # height = size_z + (N-1) * stacking_z_height = 10 + 4
    self.assertEqual(stack.get_size_z(), 14)
    # second plate sinks into the first: base at the stacking pitch (4), not at full height (10).
    self.assertEqual(stack.get_top_item().get_absolute_location(), Coordinate(0, 0, 4))

  def test_three_plates_nest(self):
    stack = ResourceStack("s", "z")
    stack.location = Coordinate.zero()
    for i in range(3):
      stack.assign_child_resource(self._plate(f"p{i}", stacking_z_height=4))
    # height = 10 + 2 * 4
    self.assertEqual(stack.get_size_z(), 18)
    self.assertEqual(stack.get_top_item().get_absolute_location(), Coordinate(0, 0, 8))

  def test_no_nesting_onto_lidded_plate(self):
    # a plate cannot nest into a plate that is wearing a lid.
    lower = self._plate("lower", stacking_z_height=4)
    lower.assign_child_resource(
      Lid("lid", size_x=10, size_y=10, size_z=3, nesting_z_height=1),
      location=Coordinate(0, 0, 0),
    )
    stack = ResourceStack("s", "z")
    stack.location = Coordinate.zero()
    stack.assign_child_resource(lower)
    stack.assign_child_resource(self._plate("upper", stacking_z_height=4))
    # lower occupies size_z + lid overhang = 10 + (3 - 1) = 12; upper sits on top at full height.
    self.assertEqual(stack.get_top_item().get_absolute_location(), Coordinate(0, 0, 12))
