import math
import unittest
import unittest.mock

from .coordinate import Coordinate
from .deck import Deck
from .errors import ResourceNotFoundError
from .resource import Resource
from .rotation import Rotation


class TestResource(unittest.TestCase):
  def test_simple_get_size(self):
    r = Resource("test", size_x=10, size_y=10, size_z=10)
    self.assertEqual(r.get_absolute_size_x(), 10)
    self.assertEqual(r.get_absolute_size_y(), 10)
    self.assertEqual(r.get_absolute_size_z(), 10)

  def test_rotated_45(self):
    r = Resource("test", size_x=20, size_y=10, size_z=10)
    r.rotation = Rotation(z=45)
    width1 = 20 * math.cos(math.radians(45)) + 10 * math.cos(math.radians(45))
    self.assertAlmostEqual(r.get_absolute_size_x(), width1, places=5)

    height1 = 20 * math.sin(math.radians(45)) + 10 * math.sin(math.radians(45))
    self.assertAlmostEqual(r.get_absolute_size_y(), height1, places=5)

  def test_rotated_m45(self):
    r = Resource("test", size_x=20, size_y=10, size_z=10)
    r.rotation = Rotation(z=-45)
    width1 = 20 * math.cos(math.radians(45)) + 10 * math.cos(math.radians(45))
    self.assertAlmostEqual(r.get_absolute_size_x(), width1, places=5)

    height1 = 20 * math.sin(math.radians(45)) + 10 * math.sin(math.radians(45))
    self.assertAlmostEqual(r.get_absolute_size_y(), height1, places=5)

  def test_get_resource(self):
    deck = Deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)

    with self.assertRaises(ResourceNotFoundError):
      deck.get_resource("not_a_resource")

  def test_assign_in_order(self):
    deck = Deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=10, size_y=10, size_z=10)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)
    self.assertEqual(child.parent, parent)
    self.assertEqual(parent.parent, deck)
    self.assertIsNone(deck.parent)

  def test_assign_build_carrier_first(self):
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    deck = Deck()
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)
    self.assertEqual(child.parent, parent)
    self.assertEqual(parent.parent, deck)
    self.assertIsNone(deck.parent)

  def test_assign_name_taken(self):
    deck = Deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    with self.assertRaises(ValueError):
      other_child = Resource("child", size_x=5, size_y=5, size_z=5)
      deck.assign_child_resource(other_child, location=Coordinate(5, 5, 5))

  def test_assign_name_exists_in_tree(self):
    root = Resource("root", size_x=10, size_y=10, size_z=10)
    child1 = Resource("child", size_x=5, size_y=5, size_z=5)
    root.assign_child_resource(child1, location=Coordinate(5, 5, 5))
    child2 = Resource("child", size_x=5, size_y=5, size_z=5)
    with self.assertRaises(ValueError):
      root.assign_child_resource(child2, location=Coordinate(5, 5, 5))

    grandchild1 = Resource("grandchild", size_x=5, size_y=5, size_z=5)
    child1.assign_child_resource(grandchild1, location=Coordinate(5, 5, 5))
    child3 = Resource("child3", size_x=5, size_y=5, size_z=5)
    root.assign_child_resource(child3, location=Coordinate(5, 5, 5))
    grandchild2 = Resource("grandchild", size_x=5, size_y=5, size_z=5)
    with self.assertRaises(ValueError):
      root.assign_child_resource(grandchild2, location=Coordinate(5, 5, 5))

  def test_get_anchor(self):
    resource = Resource("test", size_x=12, size_y=12, size_z=12)
    self.assertEqual(
      resource.get_anchor(x="left", y="back", z="bottom"),
      Coordinate(0, 12, 0),
    )
    self.assertEqual(
      resource.get_anchor(x="right", y="front", z="top"),
      Coordinate(12, 0, 12),
    )
    self.assertEqual(
      resource.get_anchor(x="center", y="center", z="center"),
      Coordinate(6, 6, 6),
    )

    self.assertEqual(resource.get_anchor(x="l", y="b", z="b"), Coordinate(0, 12, 0))
    self.assertEqual(resource.get_anchor(x="r", y="f", z="t"), Coordinate(12, 0, 12))
    self.assertEqual(resource.get_anchor(x="c", y="c", z="c"), Coordinate(6, 6, 6))

  def test_absolute_location(self):
    deck = Deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(
      deck.get_resource("parent").get_absolute_location(),
      Coordinate(10, 10, 10),
    )
    self.assertEqual(
      deck.get_resource("child").get_absolute_location(),
      Coordinate(15, 15, 15),
    )

  def test_get_absolute_location_with_anchor(self):
    deck = Deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(
      deck.get_resource("parent").get_absolute_location(x="right", y="front", z="top"),
      Coordinate(20, 10, 20),
    )
    self.assertEqual(
      deck.get_resource("child").get_absolute_location(x="right", y="front", z="top"),
      Coordinate(20, 15, 20),
    )

    single = Resource("single", size_x=5, size_y=5, size_z=5)
    single.location = Coordinate.zero()
    self.assertEqual(
      single.get_absolute_location(x="right", y="front", z="top"),
      Coordinate(5, 0, 5),
    )

  def test_unassign_child(self):
    deck = Deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))
    parent.unassign_child_resource(child)

    self.assertIsNone(child.parent)
    with self.assertRaises(ResourceNotFoundError):
      deck.get_resource("child")
    with self.assertRaises(ResourceNotFoundError):
      parent.get_resource("child")

  def test_reassign_child(self):
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent1 = Resource("parent1", size_x=10, size_y=10, size_z=10)
    parent2 = Resource("parent2", size_x=10, size_y=10, size_z=10)

    parent1.assign_child_resource(child, location=Coordinate(5, 5, 5))
    parent2.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(child.parent, parent2)
    self.assertEqual(parent1.children, [])
    self.assertEqual(parent2.children, [child])

  def test_get_all_children(self):
    deck = Deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(deck.get_all_children(), [parent, child])

  def test_eq(self):
    deck1 = Deck()
    deck2 = Deck()
    self.assertEqual(deck1, deck2)

    parent1 = Resource("parent", size_x=10, size_y=10, size_z=10)
    parent2 = Resource("parent", size_x=10, size_y=10, size_z=10)
    self.assertEqual(parent1, parent2)

    child1 = Resource("child", size_x=5, size_y=5, size_z=5)
    child2 = Resource("child", size_x=5, size_y=5, size_z=5)
    self.assertEqual(child1, child2)

  def test_serialize(self):
    r = Resource("test", size_x=10, size_y=10, size_z=10)
    self.assertEqual(
      r.serialize(),
      {
        "name": "test",
        "location": None,
        "rotation": {
          "type": "Rotation",
          "x": 0,
          "y": 0,
          "z": 0,
        },
        "size_x": 10,
        "size_y": 10,
        "size_z": 10,
        "type": "Resource",
        "children": [],
        "category": None,
        "parent_name": None,
        "model": None,
      },
    )

  def test_child_serialize(self):
    r = Resource("test", size_x=10, size_y=10, size_z=10)
    child = Resource("child", size_x=1, size_y=1, size_z=1)
    r.assign_child_resource(child, location=Coordinate(5, 5, 5))
    self.maxDiff = None
    self.assertEqual(
      r.serialize(),
      {
        "name": "test",
        "location": None,
        "rotation": {
          "type": "Rotation",
          "x": 0,
          "y": 0,
          "z": 0,
        },
        "size_x": 10,
        "size_y": 10,
        "size_z": 10,
        "type": "Resource",
        "children": [
          {
            "name": "child",
            "location": {
              "type": "Coordinate",
              "x": 5,
              "y": 5,
              "z": 5,
            },
            "rotation": {
              "type": "Rotation",
              "x": 0,
              "y": 0,
              "z": 0,
            },
            "size_x": 1,
            "size_y": 1,
            "size_z": 1,
            "type": "Resource",
            "children": [],
            "category": None,
            "parent_name": "test",
            "model": None,
          }
        ],
        "category": None,
        "parent_name": None,
        "model": None,
      },
    )

  def test_deserialize(self):
    r = Resource("test", size_x=10, size_y=10, size_z=10)
    self.assertEqual(Resource.deserialize(r.serialize()), r)

  def test_deserialize_location_none(self):
    r = Resource("test", size_x=10, size_y=10, size_z=10)
    c = Resource("child", size_x=1, size_y=1, size_z=1)
    r.assign_child_resource(c, location=Coordinate.zero())
    self.assertEqual(Resource.deserialize(r.serialize()), r)

  def test_get_center_offsets(self):
    r = Resource("test", size_x=10, size_y=120, size_z=10)
    self.assertEqual(r.centers(), [Coordinate(5.0, 60, 5.0)])
    self.assertEqual(r.centers(zn=0), [Coordinate(5.0, 60, 0.0)])

    self.assertEqual(
      r.centers(yn=2),
      [Coordinate(5.0, 40.0, 5.0), Coordinate(5.0, 80.0, 5.0)],
    )
    self.assertEqual(
      r.centers(yn=3),
      [
        Coordinate(5.0, 30.0, 5.0),
        Coordinate(5.0, 60.0, 5.0),
        Coordinate(5.0, 90.0, 5.0),
      ],
    )

  def test_rotation90(self):
    r = Resource("parent", size_x=200, size_y=100, size_z=100)
    r.location = Coordinate.zero()
    c = Resource("child", size_x=10, size_y=20, size_z=10)
    r.assign_child_resource(c, location=Coordinate(20, 10, 10))

    r.rotate(z=90)
    self.assertAlmostEqual(r.get_absolute_size_x(), 100)
    self.assertAlmostEqual(r.get_absolute_size_y(), 200)
    self.assertEqual(c.get_absolute_location(), Coordinate(-10, 20, 10))
    self.assertAlmostEqual(c.get_absolute_size_x(), 20)
    self.assertAlmostEqual(c.get_absolute_size_y(), 10)

  def test_rotation180(self):
    r = Resource("parent", size_x=200, size_y=100, size_z=100)
    r.location = Coordinate.zero()
    c = Resource("child", size_x=10, size_y=20, size_z=10)
    r.assign_child_resource(c, location=Coordinate(20, 10, 10))

    r.rotate(z=180)
    self.assertAlmostEqual(r.get_absolute_size_x(), 200)
    self.assertAlmostEqual(r.get_absolute_size_y(), 100)
    self.assertEqual(c.get_absolute_location(), Coordinate(x=-20, y=-10, z=10))
    self.assertAlmostEqual(c.get_absolute_size_x(), 10)
    self.assertAlmostEqual(c.get_absolute_size_y(), 20)

  def test_rotation270(self):
    r = Resource("parent", size_x=200, size_y=100, size_z=100)
    r.location = Coordinate.zero()
    c = Resource("child", size_x=10, size_y=20, size_z=10)
    r.assign_child_resource(c, location=Coordinate(20, 10, 10))

    r.rotate(z=270)
    self.assertAlmostEqual(r.get_absolute_size_x(), 100)
    self.assertAlmostEqual(r.get_absolute_size_y(), 200)
    self.assertEqual(c.get_absolute_location(), Coordinate(x=10, y=-20, z=10))
    self.assertAlmostEqual(c.get_absolute_size_x(), 20)
    self.assertAlmostEqual(c.get_absolute_size_y(), 10)

  def test_multiple_rotations(self):
    r = Resource("parent", size_x=200, size_y=100, size_z=100)
    r.location = Coordinate.zero()
    c = Resource("child", size_x=10, size_y=20, size_z=10)
    r.assign_child_resource(c, location=Coordinate(20, 10, 10))

    r.rotate(z=90)
    r.rotate(z=90)  # 180
    self.assertAlmostEqual(r.get_absolute_size_x(), 200)
    self.assertAlmostEqual(r.get_absolute_size_y(), 100)
    self.assertEqual(c.get_absolute_location(), Coordinate(x=-20, y=-10, z=10))

    r.rotate(z=90)  # 270
    self.assertAlmostEqual(r.get_absolute_size_x(), 100)
    self.assertAlmostEqual(r.get_absolute_size_y(), 200)
    self.assertEqual(c.get_absolute_location(), Coordinate(x=10, y=-20, z=10))

    r.rotate(z=90)  # 0
    self.assertAlmostEqual(r.get_absolute_size_x(), 200)
    self.assertAlmostEqual(r.get_absolute_size_y(), 100)
    self.assertEqual(c.get_absolute_location(), Coordinate(20, 10, 10))


class TestResourceCallback(unittest.TestCase):
  def setUp(self) -> None:
    super().setUp()
    self.r = Resource("test", size_x=10, size_y=10, size_z=10)
    self.child = Resource("child", size_x=5, size_y=5, size_z=5)

  def test_will_assign_resource(self):
    mock_function = unittest.mock.Mock()
    self.r.register_will_assign_resource_callback(mock_function)
    self.r.assign_child_resource(self.child, location=Coordinate.zero())
    mock_function.assert_called_once_with(self.child)

  def test_will_assign_resource_error(self):
    # raising an error in will assign should prevent the resource from being assigned
    mock_function = unittest.mock.Mock(side_effect=ValueError("test"))
    self.r.register_will_assign_resource_callback(mock_function)
    with self.assertRaises(ValueError):
      self.r.assign_child_resource(self.child, location=Coordinate.zero())
    self.assertEqual(self.r.children, [])
    mock_function.assert_called_once_with(self.child)

  def test_did_assign_resource(self):
    mock_function = unittest.mock.Mock()
    self.r.register_did_assign_resource_callback(mock_function)
    self.r.assign_child_resource(self.child, location=Coordinate.zero())
    mock_function.assert_called_once_with(self.child)

  def test_will_unassign_resource(self):
    mock_function = unittest.mock.Mock()
    self.r.register_will_unassign_resource_callback(mock_function)
    self.r.assign_child_resource(self.child, location=Coordinate.zero())
    mock_function.assert_not_called()
    self.r.unassign_child_resource(self.child)
    mock_function.assert_called_once_with(self.child)

  def test_did_unassign_resource(self):
    mock_function = unittest.mock.Mock()
    self.r.register_did_unassign_resource_callback(mock_function)
    self.r.assign_child_resource(self.child, location=Coordinate.zero())
    mock_function.assert_not_called()
    self.child.unassign()
    mock_function.assert_called_once_with(self.child)

  def test_callbacks_removed_on_unassign(self):
    mock_function = unittest.mock.Mock()
    self.r.register_did_unassign_resource_callback(mock_function)
    self.r.assign_child_resource(self.child, location=Coordinate.zero())
    self.child.unassign()

    self.assertEqual(self.child._did_assign_resource_callbacks, [])
    self.assertEqual(self.child._did_unassign_resource_callbacks, [])
    self.assertEqual(self.child._will_assign_resource_callbacks, [])
    self.assertEqual(self.child._will_unassign_resource_callbacks, [])

  def test_did_assign_is_passed_up_the_chain(self):
    mock_function = unittest.mock.Mock()
    self.r.register_did_assign_resource_callback(mock_function)
    self.r.assign_child_resource(self.child, location=Coordinate.zero())
    mock_function.reset_mock()
    new_child = Resource("new_child", size_x=5, size_y=5, size_z=5)
    self.child.assign_child_resource(new_child, location=Coordinate.zero())
    mock_function.assert_called_once_with(new_child)
