import unittest

from pylabrobot.resources import (
  Container,
  Lid,
  Liddable,
  Plate,
  Resource,
  hamilton_1_trough_200mL_Vb,
)


class LidTests(unittest.TestCase):
  """The Liddable mixin: any Plate or Container can host a lid, seated centred on its top."""

  def test_plate_lid_placement_unchanged(self):
    """Non-breaking: a footprint-matched lid seats origin-aligned at ``top - nesting``."""
    plate = Plate("p", size_x=127, size_y=86, size_z=14, ordered_items={})
    lid = Lid("l", size_x=127, size_y=86, size_z=10, nesting_z_height=3)
    loc = plate.get_lid_location(lid)
    self.assertEqual(
      (loc.x, loc.y, loc.z), (0, 0, 11)
    )  # == get_child_location + (0,0,size_z-nesting)

  def test_container_lid_is_centred_and_sunk(self):
    """A larger-footprint lid on a container centres on the top face and sinks by nesting."""
    c = Container("c", size_x=37, size_y=118, size_z=95, material_z_thickness=1.5)
    lid = Lid("cl", size_x=44, size_y=125, size_z=10, nesting_z_height=4)
    loc = c.get_lid_location(lid)
    self.assertEqual((loc.x, loc.y, loc.z), (-3.5, -3.5, 91))  # (37-44)/2, (118-125)/2, 95-4

  def test_hamilton_trough_larger_lid_is_centred(self):
    """A real Hamilton trough with a directly-built lid 2 mm larger in x and y: the lid centres on
    the top face, overhanging 1 mm each side."""
    trough = hamilton_1_trough_200mL_Vb(name="trough")
    lid = Lid(
      "trough_lid",
      size_x=trough.get_size_x() + 2,
      size_y=trough.get_size_y() + 2,
      size_z=10,
      nesting_z_height=4,
    )
    loc = trough.get_lid_location(lid)
    self.assertEqual((loc.x, loc.y, loc.z), (-1, -1, 91))  # (37-39)/2, (118-120)/2, 95-4
    trough.assign_child_resource(lid)
    self.assertTrue(trough.has_lid())

  def test_containers_are_liddable_lids_are_not(self):
    self.assertIsInstance(Container("c", 10, 10, 10, material_z_thickness=1), Liddable)
    self.assertIsInstance(Plate("p", 10, 10, 10, ordered_items={}), Liddable)
    self.assertNotIsInstance(Lid("l", 10, 10, 5, nesting_z_height=1), Liddable)  # can't lid a lid

  def test_double_lid_is_guarded(self):
    c = Container("c", 40, 40, 20, material_z_thickness=1)
    c.assign_child_resource(Lid("l1", 40, 40, 5, nesting_z_height=1))
    self.assertTrue(c.has_lid())
    with self.assertRaises(ValueError):
      c.assign_child_resource(Lid("l2", 40, 40, 5, nesting_z_height=1))

  def test_lid_round_trip_restores_state(self):
    c = Container("c", 40, 40, 20, material_z_thickness=1)
    c.assign_child_resource(Lid("l", 40, 40, 5, nesting_z_height=1))
    c2 = Resource.deserialize(c.serialize())
    assert isinstance(c2, Container)
    self.assertTrue(c2.has_lid())
    assert c2.lid is not None
    self.assertEqual(c2.lid.name, "l")

  def test_lid_size_check(self):
    """Reject a lid clearly smaller than the parent; allow within-tolerance-under, equal, or larger."""
    c = Container("c", 40, 40, 20, material_z_thickness=1)
    with self.assertRaises(ValueError):
      c.assign_child_resource(Lid("sx", 35, 40, 5, nesting_z_height=1))  # x well under
    with self.assertRaises(ValueError):
      c.assign_child_resource(Lid("sy", 40, 35, 5, nesting_z_height=1))  # y well under
    c.assign_child_resource(
      Lid("ok", 39.5, 45, 5, nesting_z_height=1)
    )  # 0.5mm under (x) + larger (y)
    self.assertTrue(c.has_lid())


if __name__ == "__main__":
  unittest.main()
