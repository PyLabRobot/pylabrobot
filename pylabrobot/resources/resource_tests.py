import json
import math
import re
import unittest
import unittest.mock
from typing import Any, Dict

from . import resource as resource_module
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
        "size_x": 10,
        "size_y": 10,
        "size_z": 10,
        "type": "Resource",
        "barcode": {
          "data": "1234567890",
          "symbology": "code128",
          "position_on_resource": "left",
        },
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
            "size_x": 1,
            "size_y": 1,
            "size_z": 1,
            "type": "Resource",
            "parent_name": "test",
          }
        ],
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


class TestResourceMetadata(unittest.TestCase):
  """Tests for Resource.metadata and the find_resources/find_resource query API."""

  def _sample_tree(self):
    """Build a small deck tree shared by several find_resources tests.

    Returns (deck, plate, well, trough, waste). ``plate`` contains ``well``;
    ``trough`` and ``waste`` are direct children of ``deck``.
    """
    deck = Resource("deck", size_x=100, size_y=100, size_z=10)
    plate = Resource(
      "plate1",
      size_x=10,
      size_y=10,
      size_z=10,
      category="plate",
      model="m1",
      metadata={"liquid": "water", "concentration_mM": 100, "is_clean": True, "pH": 7.0},
    )
    well = Resource("well1", size_x=1, size_y=1, size_z=1)
    trough = Resource(
      "trough1",
      size_x=10,
      size_y=10,
      size_z=10,
      category="trough",
      model="m2",
      metadata={"liquid": "buffer", "concentration_mM": 50, "is_clean": False},
    )
    waste = Resource(
      "waste1",
      size_x=10,
      size_y=10,
      size_z=10,
      category="waste",
      metadata={"liquid": "water", "concentration_mM": 0},
    )
    deck.assign_child_resource(plate, location=Coordinate(0, 0, 0))
    plate.assign_child_resource(well, location=Coordinate(0, 0, 0))
    deck.assign_child_resource(trough, location=Coordinate(20, 0, 0))
    deck.assign_child_resource(waste, location=Coordinate(40, 0, 0))
    return deck, plate, well, trough, waste

  def test_metadata_init_and_equality(self):
    r1 = Resource("r1", size_x=10, size_y=10, size_z=10, metadata={"a": 1, "is_clean": True})
    r2 = Resource("r1", size_x=10, size_y=10, size_z=10, metadata={"a": 1, "is_clean": True})
    r3 = Resource("r1", size_x=10, size_y=10, size_z=10, metadata={"a": 2, "is_clean": True})

    self.assertEqual(r1.metadata, {"a": 1, "is_clean": True})
    self.assertEqual(r1, r2)
    self.assertNotEqual(r1, r3)

  def test_metadata_default_is_empty_and_per_instance(self):
    r1 = Resource("r1", size_x=1, size_y=1, size_z=1)
    r2 = Resource("r2", size_x=1, size_y=1, size_z=1)
    self.assertEqual(r1.metadata, {})
    r1.metadata["k"] = "v"
    self.assertEqual(r2.metadata, {})

  def test_metadata_shallow_copy_semantics(self):
    # metadata.copy() decouples top-level keys from the caller's dict, but nested
    # containers are shared. This pins the documented shallow-copy behavior.
    original: dict[str, Any] = {"top": "v", "nested": [1, 2]}
    r = Resource("r", size_x=1, size_y=1, size_z=1, metadata=original)

    original["top"] = "changed"
    self.assertEqual(r.metadata["top"], "v")  # top-level key isolated

    original["nested"].append(3)
    self.assertEqual(r.metadata["nested"], [1, 2, 3])  # nested value shared

  def test_metadata_serialization_deserialization(self):
    meta = {"string": "hello", "int": 42, "bool": False, "list": [1, 2, 3], "nested": {"k": "v"}}
    r = Resource("res", size_x=10, size_y=10, size_z=10, metadata=meta)
    serialized = r.serialize()
    self.assertEqual(serialized["metadata"], meta)

    deserialized = Resource.deserialize(serialized)
    self.assertEqual(deserialized.metadata, meta)
    self.assertEqual(deserialized, r)

  def test_metadata_serialize_copy_isolation(self):
    meta = {"key": "val", "nested": [1, 2]}
    r = Resource("res", size_x=10, size_y=10, size_z=10, metadata=meta)
    serialized = r.serialize()
    serialized["metadata"]["key"] = "modified"
    self.assertEqual(r.metadata["key"], "val")

  def test_metadata_copy_isolation(self):
    meta = {"key": "val"}
    r = Resource("res", size_x=10, size_y=10, size_z=10, metadata=meta)
    r_copy = r.copy()
    r_copy.metadata["key"] = "modified"
    self.assertEqual(r.metadata["key"], "val")

  def test_metadata_black_box_roundtrip_tighter(self):
    class CustomObj:
      def __init__(self, value: int):
        self.value = value

      def __eq__(self, other: Any) -> bool:
        return isinstance(other, CustomObj) and self.value == other.value

    custom_obj = CustomObj(42)
    meta: Dict[str, Any] = {
      "custom_obj": custom_obj,
      "tuple": (1, 2, 3),
      "none": None,
      "nested_dict": {"inner_custom": CustomObj(99)},
    }
    r = Resource("res", size_x=10, size_y=10, size_z=10, metadata=meta)
    serialized = r.serialize()
    self.assertEqual(serialized["metadata"], meta)

    deserialized = Resource.deserialize(serialized)
    self.assertEqual(deserialized.metadata, meta)
    self.assertEqual(deserialized, r)

    r_copy = r.copy()
    self.assertEqual(r_copy.metadata, meta)
    self.assertEqual(r_copy, r)

  def test_metadata_type_key_serialization_deserialization(self):
    meta = {"type": "reagent", "nested": {"type": "custom_type"}}
    r = Resource("res", size_x=10, size_y=10, size_z=10, metadata=meta)
    serialized = r.serialize()
    self.assertEqual(serialized["metadata"], meta)

    deserialized = Resource.deserialize(serialized)
    self.assertEqual(deserialized.metadata, meta)
    self.assertEqual(deserialized, r)

  def test_metadata_json_roundtrip(self):
    meta = {"type": "reagent", "nested": {"type": "custom_type"}, "count": 42}
    r = Resource("res", size_x=10, size_y=10, size_z=10, metadata=meta)
    serialized_json = json.dumps(r.serialize())
    deserialized = Resource.deserialize(json.loads(serialized_json))
    self.assertEqual(deserialized.metadata, meta)
    self.assertEqual(deserialized, r)
    self.assertIsNot(deserialized.metadata, meta)

  def test_deserialize_without_metadata_key_is_backward_compatible(self):
    # Resources serialized before metadata existed have no "metadata" key.
    data = Resource("r", size_x=1, size_y=1, size_z=1).serialize()
    del data["metadata"]
    restored = Resource.deserialize(data)
    self.assertEqual(restored.metadata, {})

  def test_deserialize_non_dict_metadata_raises(self):
    data = Resource("r", size_x=1, size_y=1, size_z=1).serialize()
    data["metadata"] = ["not", "a", "dict"]
    with self.assertRaises(TypeError):
      Resource.deserialize(data)

  def test_deserialize_same_named_class_via_module_alias(self):
    # Simulate a class imported under two module paths: find_subclass returns a
    # same-named class that is NOT a subclass of the cls deserialize was called
    # on. issubclass() is False, so deserialization is rejected.
    # (Unusual class names avoid polluting the global Resource subclass registry
    # under a common name.)
    class _AliasProbe(Resource):
      pass

    class _AliasProbeSibling(Resource):  # sibling, not a subclass of _AliasProbe
      pass

    _AliasProbeSibling.__name__ = "_AliasProbe"

    data = _AliasProbe("f", size_x=1, size_y=1, size_z=1).serialize()
    with unittest.mock.patch.object(
      resource_module, "find_subclass", return_value=_AliasProbeSibling
    ):
      with self.assertRaises(AssertionError):
        _AliasProbe.deserialize(data)

  def test_deserialize_rejects_class_with_different_name(self):
    class _RejectProbe(Resource):
      pass

    class _UnrelatedProbe:  # different name -> neither issubclass nor name match
      pass

    data = _RejectProbe("f", size_x=1, size_y=1, size_z=1).serialize()
    with unittest.mock.patch.object(resource_module, "find_subclass", return_value=_UnrelatedProbe):
      with self.assertRaises(AssertionError):
        _RejectProbe.deserialize(data)

  def test_find_resources_metadata(self):
    deck, plate, well, trough, waste = self._sample_tree()

    # Strict value equality via metadata=
    self.assertEqual(deck.find_resources(metadata={"liquid": "water"}), [plate, waste])
    self.assertEqual(deck.find_resources(metadata={"is_clean": True}), [plate])
    self.assertEqual(deck.find_resources(metadata={"is_clean": False}), [trough])

    # Key presence check via has_metadata
    self.assertEqual(deck.find_resources(has_metadata="pH"), [plate])
    self.assertEqual(
      set(deck.find_resources(has_metadata=["liquid", "concentration_mM"])), {plate, trough, waste}
    )

    # Callable predicate on metadata.get(key)
    self.assertEqual(
      deck.find_resources(metadata={"concentration_mM": lambda v: v is not None and v > 20}),
      [plate, trough],
    )
    self.assertEqual(
      deck.find_resources(metadata={"pH": lambda v: v is not None and v == 7.0}), [plate]
    )
    self.assertEqual(
      set(deck.find_resources(metadata={"pH": lambda v: v is None})), {deck, well, trough, waste}
    )

    # Top-level attribute matchers (name, type, model, category)
    self.assertEqual(deck.find_resources(name="plate1"), [plate])
    self.assertEqual(deck.find_resources(name=re.compile(r"^(plate|trough)")), [plate, trough])
    self.assertEqual(deck.find_resources(category="plate"), [plate])
    self.assertEqual(deck.find_resources(model="m2"), [trough])
    self.assertEqual(set(deck.find_resources(type=Resource)), {deck, plate, well, trough, waste})

    # Custom predicate fn
    self.assertEqual(
      deck.find_resources(
        fn=lambda r: r.get_size_x() == 10 and r.metadata.get("concentration_mM") == 100
      ),
      [plate],
    )

    # find_resource singular
    self.assertEqual(deck.find_resource(name="trough1"), trough)
    self.assertIsNone(deck.find_resource(name="nonexistent"))

    # Non-recursive search
    self.assertEqual(deck.find_resources(name="plate1", recursive=False), [plate])
    self.assertEqual(deck.find_resources(name="well1", recursive=False), [])
    self.assertEqual(deck.find_resources(name="well1", recursive=True), [well])

  def test_find_resources_type_matcher_variants(self):
    # Unusual class names avoid polluting the global Resource subclass registry.
    class _TypeAlpha(Resource):
      pass

    class _TypeBeta(Resource):
      pass

    deck = Resource("deck", size_x=10, size_y=10, size_z=10)
    alpha = _TypeAlpha("alpha", size_x=1, size_y=1, size_z=1)
    beta = _TypeBeta("beta", size_x=1, size_y=1, size_z=1)
    deck.assign_child_resource(alpha, location=Coordinate(0, 0, 0))
    deck.assign_child_resource(beta, location=Coordinate(5, 0, 0))

    self.assertEqual(deck.find_resources(type=_TypeAlpha), [alpha])  # class
    self.assertEqual(
      deck.find_resources(type=(_TypeAlpha, _TypeBeta)), [alpha, beta]
    )  # tuple of classes
    self.assertEqual(deck.find_resources(type=_TypeBeta.__name__), [beta])  # class name string
    self.assertEqual(
      deck.find_resources(type=re.compile(r"_TypeA")), [alpha]
    )  # regex on class name
    self.assertEqual(
      deck.find_resources(type=lambda k: issubclass(k, _TypeBeta)), [beta]
    )  # callable receives the resource class

  def test_find_resources_attribute_callable_receives_attribute_value(self):
    deck, plate, well, trough, waste = self._sample_tree()
    # A callable for a (non-type) top-level attribute receives the attribute value.
    self.assertEqual(deck.find_resources(model=lambda m: m == "m2"), [trough])
    self.assertEqual(
      set(deck.find_resources(name=lambda n: n.endswith("1"))), {plate, well, trough, waste}
    )

  def test_find_resources_all_filters_combined(self):
    deck, plate, _well, _trough, _waste = self._sample_tree()
    result = deck.find_resources(
      fn=lambda r: r.get_size_x() == 10,
      has_metadata="is_clean",
      metadata={"liquid": "water"},
      category="plate",
    )
    self.assertEqual(result, [plate])

  def test_callable_metadata_value_is_treated_as_predicate(self):
    # A callable stored AS a metadata value cannot be matched by equality:
    # passing it as the matcher invokes it as a predicate. Use an identity
    # predicate to match such values.
    def handler(x):
      return x  # truthy for the stored object, falsy (None) when key absent

    deck = Resource("deck", size_x=10, size_y=10, size_z=10)
    child = Resource("child", size_x=1, size_y=1, size_z=1, metadata={"cb": handler})
    deck.assign_child_resource(child, location=Coordinate(0, 0, 0))

    # cb=handler runs handler(metadata.get("cb")); for child that is
    # handler(handler) -> truthy, so it "matches" -- the documented footgun.
    self.assertEqual(deck.find_resources(metadata={"cb": handler}), [child])
    # Identity match via an explicit predicate is the correct approach.
    self.assertEqual(deck.find_resources(metadata={"cb": lambda v: v is handler}), [child])

  def test_find_resource_returns_first_match_or_self(self):
    deck, plate, _well, _trough, waste = self._sample_tree()
    # Two resources match; the first encountered (self is checked first) is returned.
    self.assertEqual(deck.find_resources(metadata={"liquid": "water"}), [plate, waste])
    self.assertEqual(deck.find_resource(metadata={"liquid": "water"}), plate)
    # The search includes self, so an unfiltered/self-matching query can return it.
    self.assertIs(deck.find_resource(type=Resource), deck)
    self.assertIsNone(deck.find_resource(name="does-not-exist"))

  def test_find_resources_no_criteria_returns_self_and_descendants(self):
    deck, plate, well, trough, waste = self._sample_tree()
    # No criteria: self is first, followed by get_all_children() order (direct
    # children before deeper descendants), so `well` (under `plate`) comes last.
    self.assertEqual(deck.find_resources(), [deck, plate, trough, waste, well])
    # Non-recursive: self plus direct children only.
    self.assertEqual(deck.find_resources(recursive=False), [deck, plate, trough, waste])
