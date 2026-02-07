import math
import unittest
import unittest.mock

from .barcode import Barcode
from .coordinate import Coordinate
from .deck import Deck
from .errors import ResourceNotFoundError
from .resource import Resource
from .rotation import Rotation


def _make_test_deck() -> Deck:
  return Deck(size_x=100, size_y=100, size_z=100)


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
    deck = _make_test_deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)

    with self.assertRaises(ResourceNotFoundError):
      deck.get_resource("not_a_resource")

  def test_assign_in_order(self):
    deck = _make_test_deck()
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

    deck = _make_test_deck()
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))

    self.assertEqual(deck.get_resource("parent"), parent)
    self.assertEqual(deck.get_resource("child"), child)
    self.assertEqual(child.parent, parent)
    self.assertEqual(parent.parent, deck)
    self.assertIsNone(deck.parent)

  def test_assign_name_taken(self):
    deck = _make_test_deck()
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
    deck = _make_test_deck()
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
    deck = _make_test_deck()
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
    deck = _make_test_deck()
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
    deck = _make_test_deck()
    parent = Resource("parent", size_x=10, size_y=10, size_z=10)
    deck.assign_child_resource(parent, location=Coordinate(10, 10, 10))
    child = Resource("child", size_x=5, size_y=5, size_z=5)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 5))

    self.assertEqual(deck.get_all_children(), [parent, child])

  def test_eq(self):
    deck1 = _make_test_deck()
    deck2 = _make_test_deck()
    self.assertEqual(deck1, deck2)

    parent1 = Resource("parent", size_x=10, size_y=10, size_z=10)
    parent2 = Resource("parent", size_x=10, size_y=10, size_z=10)
    self.assertEqual(parent1, parent2)

    child1 = Resource("child", size_x=5, size_y=5, size_z=5)
    child2 = Resource("child", size_x=5, size_y=5, size_z=5)
    self.assertEqual(child1, child2)

  def test_serialize(self):
    r = Resource(
      "test",
      size_x=10,
      size_y=10,
      size_z=10,
      barcode=Barcode(data="1234567890", symbology="code128", position_on_resource="left"),
    )
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
        "barcode": {
          "data": "1234567890",
          "symbology": "code128",
          "position_on_resource": "left",
        },
        "preferred_pickup_location": None,
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
            "barcode": None,
            "preferred_pickup_location": None,
          }
        ],
        "category": None,
        "parent_name": None,
        "model": None,
        "barcode": None,
        "preferred_pickup_location": None,
      },
    )

  def test_deserialize(self):
    r = Resource(
      "test",
      size_x=10,
      size_y=10,
      size_z=10,
      barcode=Barcode(data="1234567890", symbology="code128", position_on_resource="left"),
    )
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


class TestAssignChildByAnchor(unittest.TestCase):
  def setUp(self) -> None:
    super().setUp()
    self.parent = Resource("parent", size_x=100, size_y=100, size_z=10)
    self.parent.location = Coordinate.zero()  # Set location for absolute position tests

  def test_center_center_bottom_alignment(self):
    """Test aligning center-center-bottom of both parent and child."""
    child = Resource("child", size_x=80, size_y=60, size_z=5)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("c", "c", "b"),
      child_anchor=("c", "c", "b"),
    )
    # Parent CCB is at (50, 50, 0) relative to parent LFB
    # Child CCB should be at (40, 30, 0) relative to child LFB
    # So child LFB should be at (50-40, 50-30, 0-0) = (10, 20, 0)
    self.assertEqual(child.location, Coordinate(10, 20, 0))
    # Check absolute positions match
    parent_ccb = self.parent.get_absolute_location(x="c", y="c", z="b")
    child_ccb = child.get_absolute_location(x="c", y="c", z="b")
    self.assertEqual(parent_ccb, child_ccb)

  def test_left_front_bottom_alignment(self):
    """Test aligning left-front-bottom of both (should be at origin)."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("l", "f", "b"),
      child_anchor=("l", "f", "b"),
    )
    # Both LFB anchors are at (0, 0, 0) relative to their own LFB
    self.assertEqual(child.location, Coordinate(0, 0, 0))

  def test_default_anchor_is_lfb(self):
    """Test that the default anchor is left-front-bottom."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    self.parent.assign_child_by_anchor(child)
    # Default should be LFB for both, so child should be at origin
    self.assertEqual(child.location, Coordinate(0, 0, 0))

  def test_right_back_top_alignment(self):
    """Test aligning right-back-top of both."""
    child = Resource("child", size_x=60, size_y=40, size_z=8)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("r", "b", "t"),
      child_anchor=("r", "b", "t"),
    )
    # Parent RBT is at (100, 100, 10)
    # Child RBT is at (60, 40, 8)
    # Child LFB should be at (100-60, 100-40, 10-8) = (40, 60, 2)
    self.assertEqual(child.location, Coordinate(40, 60, 2))
    parent_rbt = self.parent.get_absolute_location(x="r", y="b", z="t")
    child_rbt = child.get_absolute_location(x="r", y="b", z="t")
    self.assertEqual(parent_rbt, child_rbt)

  def test_stacking_on_top(self):
    """Test stacking child on top of parent by aligning parent's top with child's bottom."""
    child = Resource("child", size_x=100, size_y=100, size_z=5)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("l", "f", "t"),
      child_anchor=("l", "f", "b"),
    )
    # Parent LFT is at (0, 0, 10)
    # Child LFB is at (0, 0, 0)
    # Child LFB should be at (0-0, 0-0, 10-0) = (0, 0, 10)
    self.assertEqual(child.location, Coordinate(0, 0, 10))
    parent_lft = self.parent.get_absolute_location(x="l", y="f", z="t")
    child_lfb = child.get_absolute_location(x="l", y="f", z="b")
    self.assertEqual(parent_lft, child_lfb)

  def test_centered_stacking(self):
    """Test stacking with center alignment in x and y."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("c", "c", "t"),
      child_anchor=("c", "c", "b"),
    )
    # Parent CCT is at (50, 50, 10)
    # Child CCB is at (25, 25, 0)
    # Child LFB should be at (50-25, 50-25, 10-0) = (25, 25, 10)
    self.assertEqual(child.location, Coordinate(25, 25, 10))

  def test_mixed_anchors(self):
    """Test various mixed anchor combinations."""
    child = Resource("child", size_x=30, size_y=20, size_z=4)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("l", "c", "b"),
      child_anchor=("r", "c", "t"),
    )
    # Parent LCB is at (0, 50, 0)
    # Child RCT is at (30, 10, 4)
    # Child LFB should be at (0-30, 50-10, 0-4) = (-30, 40, -4)
    self.assertEqual(child.location, Coordinate(-30, 40, -4))
    parent_lcb = self.parent.get_absolute_location(x="l", y="c", z="b")
    child_rct = child.get_absolute_location(x="r", y="c", z="t")
    self.assertEqual(parent_lcb, child_rct)

  def test_all_x_anchor_combinations(self):
    """Test all x-axis anchor combinations."""
    for parent_x, child_x in [
      ("l", "l"),
      ("l", "c"),
      ("l", "r"),
      ("c", "l"),
      ("c", "c"),
      ("c", "r"),
      ("r", "l"),
      ("r", "c"),
      ("r", "r"),
    ]:
      with self.subTest(parent_x=parent_x, child_x=child_x):
        child = Resource(f"child_{parent_x}_{child_x}", size_x=40, size_y=40, size_z=5)
        parent = Resource("parent_temp", size_x=100, size_y=100, size_z=10)
        parent.location = Coordinate.zero()
        parent.assign_child_by_anchor(
          child,
          parent_anchor=(parent_x, "c", "b"),
          child_anchor=(child_x, "c", "b"),
        )
        # Verify anchors align
        parent_anchor_pos = parent.get_absolute_location(x=parent_x, y="c", z="b")
        child_anchor_pos = child.get_absolute_location(x=child_x, y="c", z="b")
        self.assertEqual(parent_anchor_pos, child_anchor_pos)

  def test_all_y_anchor_combinations(self):
    """Test all y-axis anchor combinations."""
    for parent_y, child_y in [
      ("f", "f"),
      ("f", "c"),
      ("f", "b"),
      ("c", "f"),
      ("c", "c"),
      ("c", "b"),
      ("b", "f"),
      ("b", "c"),
      ("b", "b"),
    ]:
      with self.subTest(parent_y=parent_y, child_y=child_y):
        child = Resource(f"child_{parent_y}_{child_y}", size_x=40, size_y=40, size_z=5)
        parent = Resource("parent_temp", size_x=100, size_y=100, size_z=10)
        parent.location = Coordinate.zero()
        parent.assign_child_by_anchor(
          child,
          parent_anchor=("c", parent_y, "b"),
          child_anchor=("c", child_y, "b"),
        )
        # Verify anchors align
        parent_anchor_pos = parent.get_absolute_location(x="c", y=parent_y, z="b")
        child_anchor_pos = child.get_absolute_location(x="c", y=child_y, z="b")
        self.assertEqual(parent_anchor_pos, child_anchor_pos)

  def test_all_z_anchor_combinations(self):
    """Test all z-axis anchor combinations."""
    for parent_z, child_z in [
      ("b", "b"),
      ("b", "c"),
      ("b", "t"),
      ("c", "b"),
      ("c", "c"),
      ("c", "t"),
      ("t", "b"),
      ("t", "c"),
      ("t", "t"),
    ]:
      with self.subTest(parent_z=parent_z, child_z=child_z):
        child = Resource(f"child_{parent_z}_{child_z}", size_x=40, size_y=40, size_z=5)
        parent = Resource("parent_temp", size_x=100, size_y=100, size_z=10)
        parent.location = Coordinate.zero()
        parent.assign_child_by_anchor(
          child,
          parent_anchor=("c", "c", parent_z),
          child_anchor=("c", "c", child_z),
        )
        # Verify anchors align
        parent_anchor_pos = parent.get_absolute_location(x="c", y="c", z=parent_z)
        child_anchor_pos = child.get_absolute_location(x="c", y="c", z=child_z)
        self.assertEqual(parent_anchor_pos, child_anchor_pos)

  def test_reassign_parameter(self):
    """Test that reassign parameter is passed through correctly."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    parent2 = Resource("parent2", size_x=100, size_y=100, size_z=10)
    parent2.location = Coordinate.zero()

    self.parent.assign_child_by_anchor(
      child, parent_anchor=("c", "c", "b"), child_anchor=("c", "c", "b")
    )
    self.assertEqual(child.parent, self.parent)

    # Reassigning to a different parent should work with reassign=True
    parent2.assign_child_by_anchor(
      child, parent_anchor=("l", "f", "b"), child_anchor=("l", "f", "b"), reassign=True
    )
    self.assertEqual(child.parent, parent2)
    self.assertEqual(child.location, Coordinate(0, 0, 0))

  def test_callbacks_triggered(self):
    """Test that assignment callbacks are triggered."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    mock_function = unittest.mock.Mock()
    self.parent.register_did_assign_resource_callback(mock_function)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("c", "c", "b"),
      child_anchor=("c", "c", "b"),
    )
    mock_function.assert_called_once_with(child)

  def test_long_form_anchor_names(self):
    """Test that long-form anchor names (left, center, right, etc.) work."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor=("center", "center", "bottom"),
      child_anchor=("center", "center", "bottom"),
    )
    # Should work the same as ("c", "c", "b")
    self.assertEqual(child.location, Coordinate(25, 25, 0))
    parent_ccb = self.parent.get_absolute_location(x="c", y="c", z="b")
    child_ccb = child.get_absolute_location(x="c", y="c", z="b")
    self.assertEqual(parent_ccb, child_ccb)

  def test_string_anchor_syntax(self):
    """Test that 3-character string anchor syntax works."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor="ccb",
      child_anchor="ccb",
    )
    # Should work the same as ("c", "c", "b")
    self.assertEqual(child.location, Coordinate(25, 25, 0))
    parent_ccb = self.parent.get_absolute_location(x="c", y="c", z="b")
    child_ccb = child.get_absolute_location(x="c", y="c", z="b")
    self.assertEqual(parent_ccb, child_ccb)

  def test_string_anchor_stacking(self):
    """Test string syntax for stacking resources."""
    child = Resource("child", size_x=100, size_y=100, size_z=5)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor="lft",
      child_anchor="lfb",
    )
    self.assertEqual(child.location, Coordinate(0, 0, 10))
    parent_lft = self.parent.get_absolute_location(x="l", y="f", z="t")
    child_lfb = child.get_absolute_location(x="l", y="f", z="b")
    self.assertEqual(parent_lft, child_lfb)

  def test_mixed_string_and_tuple_anchors(self):
    """Test mixing string and tuple anchor syntax."""
    child = Resource("child", size_x=60, size_y=40, size_z=8)
    self.parent.assign_child_by_anchor(
      child,
      parent_anchor="rbt",
      child_anchor=("r", "b", "t"),
    )
    # Parent RBT is at (100, 100, 10)
    # Child RBT is at (60, 40, 8)
    # Child LFB should be at (100-60, 100-40, 10-8) = (40, 60, 2)
    self.assertEqual(child.location, Coordinate(40, 60, 2))
    parent_rbt = self.parent.get_absolute_location(x="r", y="b", z="t")
    child_rbt = child.get_absolute_location(x="r", y="b", z="t")
    self.assertEqual(parent_rbt, child_rbt)

  def test_invalid_string_anchor_length(self):
    """Test that invalid string anchor lengths raise errors."""
    child = Resource("child", size_x=50, size_y=50, size_z=5)
    with self.assertRaises(ValueError) as context:
      self.parent.assign_child_by_anchor(child, parent_anchor="cc", child_anchor="ccb")
    self.assertIn("must be exactly 3 characters", str(context.exception))

    with self.assertRaises(ValueError) as context:
      self.parent.assign_child_by_anchor(child, parent_anchor="ccb", child_anchor="ccbb")
    self.assertIn("must be exactly 3 characters", str(context.exception))
