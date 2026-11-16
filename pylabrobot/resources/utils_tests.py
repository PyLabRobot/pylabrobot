import math

import pytest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.utils import (
  compute_circle_child_poses,
  create_ordered_items_2d,
  label_to_row_index,
  query,
  row_index_to_label,
  split_identifier,
)
from pylabrobot.resources.well import Well


def test_query():
  root = Resource(name="root", size_x=10, size_y=10, size_z=10)
  child1 = Resource(name="child1", size_x=5, size_y=5, size_z=5)
  child2 = Resource(name="child2", size_x=5, size_y=5, size_z=5)
  child3 = Resource(name="odd", size_x=5, size_y=5, size_z=5)
  root.assign_child_resource(child1, location=Coordinate(0, 0, 0))
  root.assign_child_resource(child2, location=Coordinate(5, 0, 0))
  root.assign_child_resource(child3, location=Coordinate(0, 5, 0))

  assert query(root, Resource) == [child1, child2, child3]
  assert query(root, Resource, x=0) == [child1, child3]
  assert query(root, Resource, y=0) == [child1, child2]
  assert query(root, name=r"child\d") == [child1, child2]


def test_query_with_type():
  root = Resource(name="root", size_x=10, size_y=10, size_z=10)
  well1 = Well(name="well1", size_x=3, size_y=3, size_z=3)
  well2 = Well(name="well2", size_x=3, size_y=3, size_z=3)
  root.assign_child_resource(well1, location=Coordinate(6, 1, 0))
  root.assign_child_resource(well2, location=Coordinate(6, 6, 0))
  assert query(root, Well) == [well1, well2]
  assert query(root, Well, x=6) == [well1, well2]


class TestRowLabels:
  def test_single_letter(self):
    assert row_index_to_label(0) == "A"
    assert row_index_to_label(7) == "H"
    assert row_index_to_label(25) == "Z"

  def test_double_letter(self):
    assert row_index_to_label(26) == "AA"
    assert row_index_to_label(27) == "AB"
    assert row_index_to_label(31) == "AF"

  def test_negative_raises(self):
    with pytest.raises(ValueError):
      row_index_to_label(-1)

  def test_roundtrip(self):
    for i in range(52):
      assert label_to_row_index(row_index_to_label(i)) == i

  def test_label_to_index(self):
    assert label_to_row_index("A") == 0
    assert label_to_row_index("Z") == 25
    assert label_to_row_index("AA") == 26
    assert label_to_row_index("AF") == 31

  def test_label_case_insensitive(self):
    assert label_to_row_index("a") == 0
    assert label_to_row_index("af") == 31

  def test_split_identifier(self):
    assert split_identifier("A1") == ("A", "1")
    assert split_identifier("AF48") == ("AF", "48")
    assert split_identifier("P24") == ("P", "24")

  def test_split_identifier_no_digits_raises(self):
    with pytest.raises(ValueError):
      split_identifier("ABC")


def test_deep():
  root = Resource(name="root", size_x=10, size_y=10, size_z=10)
  child1 = Resource(name="child1", size_x=5, size_y=5, size_z=5)
  grandchild = Resource(name="grandchild", size_x=2, size_y=2, size_z=2)
  root.assign_child_resource(child1, location=Coordinate(0, 0, 0))
  child1.assign_child_resource(grandchild, location=Coordinate(1, 1, 0))

  assert query(root, Resource) == [child1, grandchild]


def test_item_spacing_none_for_single_item_dimension():
  """item_dx/item_dy=None declares a dimension that holds a single item: the item
  is placed at (dx, dy, dz) with no spacing, and passing None where the dimension
  spans more than one item raises ValueError."""
  # None on a 1x1 grid -> the sole item sits at the origin offset, no phantom shift
  items = create_ordered_items_2d(
    Well,
    num_items_x=1,
    num_items_y=1,
    dx=5,
    dy=6,
    dz=7,
    item_dx=None,
    item_dy=None,
    size_x=8,
    size_y=8,
    size_z=8,
  )
  assert items["A1"].location == Coordinate(5, 6, 7)

  # None where the dimension actually spans (>1 items) is a misuse, not "no spacing"
  with pytest.raises(ValueError):
    create_ordered_items_2d(
      Well,
      num_items_x=2,
      num_items_y=1,
      dx=0,
      dy=0,
      dz=0,
      item_dx=None,
      item_dy=None,
      size_x=8,
      size_y=8,
      size_z=8,
    )


def test_compute_circle_child_poses():
  poses = compute_circle_child_poses(4, 3, z=2)
  expected = [((6, 3), 90), ((3, 6), 180), ((0, 3), 270), ((3, 0), 0)]
  for (p, rotation_z), ((x, y), expected_rotation_z) in zip(poses, expected):
    assert p.x == pytest.approx(x)
    assert p.y == pytest.approx(y)
    assert p.z == 2
    assert rotation_z == pytest.approx(expected_rotation_z)
  with pytest.raises(ValueError):
    compute_circle_child_poses(0, 3)


@pytest.mark.parametrize("orientation, sign", [("outwards", 1), ("inwards", -1)])
def test_compute_circle_child_poses_children_face_orientation(orientation, sign):
  parent = Resource(name="parent", size_x=80, size_y=80, size_z=10)
  poses = compute_circle_child_poses(8, 40, orientation=orientation)
  for i, (p, rotation_z) in enumerate(poses):
    child = Resource(name=f"child_{i}", size_x=8, size_y=12, size_z=5)
    center = child.get_anchor("c", "c", "b")
    parent.assign_child_resource(child, location=p - center)
    child.rotate(z=rotation_z, pivot_coordinate=center)
    child_center = child.get_absolute_location("c", "c", "b")
    front = child.get_absolute_location("c", "f", "b")
    assert child_center.x == pytest.approx(p.x)
    assert child_center.y == pytest.approx(p.y)
    expected = (sign * (child_center.x - 40), sign * (child_center.y - 40))
    facing = (front.x - child_center.x, front.y - child_center.y)
    angle = math.degrees(math.atan2(facing[1], facing[0]) - math.atan2(expected[1], expected[0]))
    assert (angle + 180) % 360 - 180 == pytest.approx(0, abs=1e-3)


def test_compute_circle_child_poses_without_orientation():
  poses = compute_circle_child_poses(4, 3, orientation=None)
  assert [rotation_z for _, rotation_z in poses] == [0.0] * 4
  assert [(p.x, p.y) for p, _ in poses] == [(p.x, p.y) for p, _ in compute_circle_child_poses(4, 3)]


def test_compute_circle_child_poses_rejects_unknown_orientation():
  with pytest.raises(ValueError):
    compute_circle_child_poses(4, 3, orientation="sideways")  # type: ignore[arg-type]
