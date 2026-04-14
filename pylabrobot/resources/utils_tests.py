import pytest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.utils import (
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
