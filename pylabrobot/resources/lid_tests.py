import math
import unittest

from pylabrobot.resources import (
  Container,
  Coordinate,
  Lid,
  Liddable,
  Plate,
  Resource,
  Rotation,
  TipRack,
  cor_96_wellplate_360uL_Fb,
  hamilton_1_trough_200mL_Vb,
)
from pylabrobot.resources.errors import NoLocationError


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


class OccupiedZTests(unittest.TestCase):
  """The vertical bounds and extent of a body and its lid along the body's local Z axis."""

  def setUp(self):
    # A 20 mm body under a 6 mm lid that nests 2 mm, so the seated lid spans 18 to 24 mm.
    self.plate = Plate("plate", 40, 30, 20, ordered_items={}, stacking_z_height=12)
    self.lid = Lid("lid", 40, 30, 6, nesting_z_height=2)

  def test_bare_and_seated_lid_preserve_body_geometry(self):
    self.plate.location = Coordinate(0, 0, 100)
    self.assertEqual(self.plate.get_occupied_z_bounds(), (0, 20))
    self.assertEqual(self.plate.get_occupied_size_z(), 20)
    self.plate.lid = self.lid
    self.assertEqual(self.plate.get_occupied_z_bounds(), (0, 24))
    self.assertEqual(self.plate.get_occupied_size_z(), 24)
    self.assertEqual(self.plate.get_size_z(), 20)
    self.assertEqual(self.plate.get_absolute_size_z(), 20)
    self.assertEqual(self.plate.get_anchor(z="t").z, 20)
    self.assertEqual(self.plate.get_absolute_location(z="t").z, 120)
    self.assertEqual(self.plate.stacking_z_height, 12)

  def test_corning_seated_lid(self):
    # 14.2 mm body; the 8.9 mm lid nests 7.6 mm, so it sits at 6.6 mm and reaches 15.5 mm.
    plate = cor_96_wellplate_360uL_Fb("corning", with_lid=True)
    assert plate.lid is not None
    self.assertEqual(plate.lid.location, Coordinate(0, 0, 6.6))
    bottom, top = plate.get_occupied_z_bounds()
    self.assertEqual(bottom, 0)
    self.assertAlmostEqual(top, 15.5)
    self.assertAlmostEqual(plate.get_occupied_size_z(), 15.5)

  def test_explicit_lid_location(self):
    # The 6 mm lid spans z to z + 6 mm, the body 0 to 20 mm.
    self.plate.assign_child_resource(self.lid, location=Coordinate(7, -8, 25))
    for z, bounds, size_z in [
      (25, (0, 31), 31),  # above the body
      (3, (0, 20), 20),  # inside the body
      (-4, (-4, 20), 24),  # across the body's bottom: the extent exceeds the top
      (-10, (-10, 20), 30),  # entirely below the body
    ]:
      with self.subTest(z=z):
        self.lid.location = Coordinate(7, -8, z)
        self.assertEqual(self.plate.get_occupied_z_bounds(), bounds)
        self.assertEqual(self.plate.get_occupied_size_z(), size_z)

  def test_lid_changes_are_read_from_current_geometry(self):
    self.plate.lid = self.lid
    self.assertEqual(self.plate.get_occupied_size_z(), 24)
    self.lid.location = Coordinate(0, 0, 25)
    self.assertEqual(self.plate.get_occupied_size_z(), 31)
    self.lid.unassign()
    self.assertEqual(self.plate.get_occupied_size_z(), 20)
    # A 10 mm lid nesting 3 mm sits at 17 mm and reaches 27 mm.
    self.plate.lid = Lid("replacement", 40, 30, 10, nesting_z_height=3)
    self.assertEqual(self.plate.get_occupied_size_z(), 27)
    self.plate.lid = None
    self.assertEqual(self.plate.get_occupied_size_z(), 20)

  def test_shared_by_container_and_tip_rack(self):
    for resource in [
      Container("container", 40, 30, 20),
      TipRack("rack", 40, 30, 20, ordered_items={}),
    ]:
      with self.subTest(resource=resource.name):
        self.assertEqual(resource.get_occupied_z_bounds(), (0, 20))
        resource.lid = self.lid
        self.assertEqual(resource.get_occupied_z_bounds(), (0, 24))
        self.lid.unassign()

  def test_other_children_and_lid_children_are_excluded(self):
    self.plate.assign_child_resource(Resource("other", 1, 1, 100), Coordinate(0, 0, 50))
    self.assertEqual(self.plate.get_occupied_z_bounds(), (0, 20))
    self.plate.lid = self.lid
    self.lid.assign_child_resource(Resource("on_lid", 1, 1, 100), Coordinate(0, 0, 50))
    self.assertEqual(self.plate.get_occupied_z_bounds(), (0, 24))

  def test_lid_rotation_is_relative_to_body(self):
    # The 40 x 30 x 6 mm lid is placed 2 mm above the body's origin and rotated right-handedly
    # about its own origin, which gives these Z ranges of the lid before the 2 mm offset:
    #   z=45: unchanged, 0 to 6.         x=90: z' = y, 0 to 30.      y=90: z' = -x, -40 to 0.
    #   x=180: z' = -z, -6 to 0.         x=45: z' = (y + z) / sqrt(2), 0 to 36 / sqrt(2).
    self.plate.assign_child_resource(self.lid, Coordinate(0, 0, 2))
    for rotation, (bottom, top) in [
      (Rotation(z=45), (0, 20)),
      (Rotation(x=90), (0, 32)),
      (Rotation(y=90), (-38, 20)),
      (Rotation(x=180), (-4, 20)),
      (Rotation(x=45), (0, 2 + 36 / math.sqrt(2))),
    ]:
      with self.subTest(rotation=rotation):
        self.lid.rotation = rotation
        actual_bottom, actual_top = self.plate.get_occupied_z_bounds()
        self.assertAlmostEqual(actual_bottom, bottom, places=3)
        self.assertAlmostEqual(actual_top, top, places=3)
        self.assertAlmostEqual(self.plate.get_occupied_size_z(), top - bottom, places=3)

  def test_assembly_translation_and_rotation_do_not_change_local_bounds(self):
    self.plate.lid = self.lid
    self.assertIsNone(self.plate.location)
    self.assertEqual(self.plate.get_occupied_z_bounds(), (0, 24))
    parent = Resource("parent", 100, 100, 100)
    parent.assign_child_resource(self.plate, Coordinate(10, 20, 50))
    parent.location = Coordinate(100, 200, 300)
    self.plate.rotation = Rotation(y=40)
    for rotation in [Rotation(z=90), Rotation(x=90), Rotation(x=30, y=20, z=10)]:
      with self.subTest(rotation=rotation):
        parent.rotation = rotation
        self.assertEqual(self.plate.get_occupied_z_bounds(), (0, 24))
        # Located through the rotated assembly, the lid still spans 18 to 24 mm along the body's Z.
        lid_zs = [
          self.lid.get_location_wrt(self.plate, x=x, y=y, z=z).z
          for x in ("l", "r")
          for y in ("f", "b")
          for z in ("b", "t")
        ]
        self.assertAlmostEqual(min(lid_zs), 18, places=3)
        self.assertAlmostEqual(max(lid_zs), 24, places=3)

  def test_attached_lid_requires_a_location(self):
    self.plate.lid = self.lid
    self.lid.location = None
    with self.assertRaises(NoLocationError):
      self.plate.get_occupied_z_bounds()
    with self.assertRaises(NoLocationError):
      self.plate.get_occupied_size_z()


if __name__ == "__main__":
  unittest.main()
