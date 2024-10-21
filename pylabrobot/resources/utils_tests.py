from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.utils import query
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
  well1 = Well(name="well", size_x=3, size_y=3, size_z=3)
  well2 = Well(name="well", size_x=3, size_y=3, size_z=3)
  root.assign_child_resource(well1, location=Coordinate(6, 1, 0))
  root.assign_child_resource(well2, location=Coordinate(6, 6, 0))
  assert query(root, Well) == [well1, well2]
  assert query(root, Well, x=6) == [well1, well2]

def test_deep():
  root = Resource(name="root", size_x=10, size_y=10, size_z=10)
  child1 = Resource(name="child1", size_x=5, size_y=5, size_z=5)
  grandchild = Resource(name="grandchild", size_x=2, size_y=2, size_z=2)
  root.assign_child_resource(child1, location=Coordinate(0, 0, 0))
  child1.assign_child_resource(grandchild, location=Coordinate(1, 1, 0))

  assert query(root, Resource) == [child1, grandchild]
