import unittest

from .coordinate import Coordinate
from .resource import Resource
from .resource_holder import ResourceHolder


class ResourceHolderTests(unittest.TestCase):
  def setUp(self):
    self.holder = ResourceHolder(name="holder", size_x=10, size_y=10, size_z=10)
    self.resource = Resource("res", size_x=1, size_y=1, size_z=1)
    self.other = Resource("other", size_x=1, size_y=1, size_z=1)

  def test_assign_via_property(self):
    self.holder.resource = self.resource
    self.assertEqual(self.holder.resource, self.resource)
    self.assertEqual(self.resource.parent, self.holder)

  def test_assign_with_yaw_rounding_error(self):
    self.resource.rotation.z = 269.999999999999

    self.holder.assign_child_resource(self.resource)

    self.assertEqual(self.resource.location, Coordinate(0, 1, 0))
    self.assertEqual(self.resource.rotation.z, 269.999999999999)

  def test_reject_misaligned_yaw(self):
    self.resource.rotation.z = 269.99

    with self.assertRaisesRegex(ValueError, "multiple of 90 degrees"):
      self.holder.assign_child_resource(self.resource)

  def test_over_assignment(self):
    self.holder.resource = self.resource
    with self.assertRaises(ValueError):
      self.holder.resource = self.other

  def test_unassign_with_none(self):
    self.holder.resource = self.resource
    self.holder.resource = None
    self.assertIsNone(self.holder.resource)
    self.assertIsNone(self.resource.parent)

  def test_assign_none_when_empty(self):
    self.holder.resource = None
    self.assertIsNone(self.holder.resource)


if __name__ == "__main__":
  unittest.main()
