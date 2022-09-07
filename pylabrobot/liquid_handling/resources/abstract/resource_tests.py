""" Tests for Resource """
# pylint: disable=missing-class-docstring

import unittest

from .coordinate import Coordinate
from .deck import Deck
from .resource import Resource


class TestResource(unittest.TestCase):
  def test_assign_in_order(self):
    deck = Deck()
    parent = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent)
    child = Resource("child", location=Coordinate(5, 5, 5), size_x=10, size_y=10, size_z=10)
    parent.assign_child_resource(child)

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)
    self.assertEqual(child.parent, parent)
    self.assertEqual(parent.parent, deck)
    self.assertIsNone(deck.parent)

  def test_assign_build_carrier_first(self):
    parent = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    child = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child)

    deck = Deck()
    deck.assign_child_resource(parent)

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)
    self.assertEqual(child.parent, parent)
    self.assertEqual(parent.parent, deck)
    self.assertIsNone(deck.parent)

  def test_assign_name_taken(self):
    deck = Deck()
    parent = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent)
    child = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child)

    with self.assertRaises(ValueError):
      other_child = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
      deck.assign_child_resource(other_child)

  def test_absolute_location(self):
    deck = Deck()
    parent = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent)
    child = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child)

    self.assertEqual(deck.get_resource("parent").get_absolute_location(), Coordinate(10, 10, 10))
    self.assertEqual(deck.get_resource("child").get_absolute_location(), Coordinate(15, 15, 15))

  def test_unassign_child(self):
    deck = Deck()
    parent = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent)
    child = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child)
    parent.unassign_child_resource(child)

    self.assertIsNone(child.parent)
    self.assertIsNone(parent.get_resource("child"))
    self.assertIsNone(deck.get_resource("child"))

  def test_get_all_children(self):
    deck = Deck()
    parent = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent)
    child = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child)

    self.assertEqual(deck.get_all_children(), [parent, child])

  def test_get_resource(self):
    deck = Deck()
    parent = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent)
    child = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child)

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)

  def test_eq(self):
    deck1 = Deck()
    deck2 = Deck()
    self.assertEqual(deck1, deck2)

    parent1 = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    parent2 = Resource("parent", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    self.assertEqual(parent1, parent2)

    child1 = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    child2 = Resource("child", location=Coordinate(5, 5, 5), size_x=5, size_y=5, size_z=5)
    self.assertEqual(child1, child2)

  def test_serialize(self):
    r = Resource("test", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    self.assertEqual(r.serialize(), {
      "name": "test",
      "location": {
        "x": 10,
        "y": 10,
        "z": 10
      },
      "size_x": 10,
      "size_y": 10,
      "size_z": 10,
      "type": "Resource",
      "children": [],
      "category": None,
      "parent_name": None
    })

  def test_deserialize(self):
    r = Resource("test", location=Coordinate(10, 10, 10), size_x=10, size_y=10, size_z=10)
    self.assertEqual(Resource.deserialize(r.serialize()), r)


if __name__ == "__main__":
  unittest.main()
