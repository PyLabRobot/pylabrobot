"""Unit tests for :mod:`.resource`."""

from __future__ import annotations

import unittest

from pylabrobot.resources import Trash, cor_96_wellplate_360uL_Fb, opentrons_96_tiprack_300ul

from ..types import MAX_LOCATIONS, MIN_LOCATION
from .geometry import well_center_offset_from_teachpoint_mm
from .resource import BravoDeck, labware_from_resource
from .teachpoints import Teachpoints


class SiteOriginFromTeachpointsTests(unittest.TestCase):
  """Every site's origin must come from the taught X/Y/Z, unchanged."""

  def test_every_site_origin_matches_its_teachpoint(self):
    deck = BravoDeck(head_type="96_d_70")
    for site in range(MIN_LOCATION, MAX_LOCATIONS + 1):
      holder = deck._site_holders[site]
      assert holder.location is not None
      expected_x = deck.teachpoints.get_teachpoint(site, "x")
      expected_y = deck.teachpoints.get_teachpoint(site, "y")
      expected_z = deck.teachpoints.get_teachpoint(site, "z")
      self.assertAlmostEqual(holder.location.x, expected_x)
      self.assertAlmostEqual(holder.location.y, expected_y)
      self.assertAlmostEqual(holder.location.z, expected_z)

  def test_uses_the_injected_teachpoints_instance_directly(self):
    teachpoints = Teachpoints()
    teachpoints.set_default_teachpoints("96_d_70")
    teachpoints.set_teachpoint(1, "x", 999.0)
    deck = BravoDeck(teachpoints=teachpoints)
    self.assertIs(deck.teachpoints, teachpoints)
    holder = deck._site_holders[1]
    assert holder.location is not None
    self.assertEqual(holder.location.x, 999.0)


class SiteResourceMappingTests(unittest.TestCase):
  """Bidirectional site<->resource lookup."""

  def test_assign_then_lookup_both_directions(self):
    deck = BravoDeck()
    plate = cor_96_wellplate_360uL_Fb(name="my_plate")
    deck.assign_child_at_site(plate, 3)
    self.assertIs(deck.resource_at_site(3), plate)
    self.assertEqual(deck.site_for_resource(plate), 3)

  def test_empty_site_has_no_resource_and_no_reverse_mapping(self):
    deck = BravoDeck()
    self.assertIsNone(deck.resource_at_site(5))
    plate = cor_96_wellplate_360uL_Fb(name="unassigned_plate")
    self.assertIsNone(deck.site_for_resource(plate))

  def test_unassign_clears_the_site(self):
    deck = BravoDeck()
    plate = cor_96_wellplate_360uL_Fb(name="my_plate")
    deck.assign_child_at_site(plate, 4)
    deck.unassign_site(4)
    self.assertIsNone(deck.resource_at_site(4))
    self.assertIsNone(deck.site_for_resource(plate))

  def test_reassigning_an_occupied_site_raises(self):
    deck = BravoDeck()
    deck.assign_child_at_site(cor_96_wellplate_360uL_Fb(name="p1"), 2)
    with self.assertRaises(ValueError):
      deck.assign_child_at_site(cor_96_wellplate_360uL_Fb(name="p2"), 2)


class OutOfRangeSiteRejectedTests(unittest.TestCase):
  """Sites outside 1-9 are rejected, not silently accepted."""

  def test_assign_at_site_zero_raises(self):
    deck = BravoDeck()
    with self.assertRaises(ValueError):
      deck.assign_child_at_site(cor_96_wellplate_360uL_Fb(name="p1"), 0)

  def test_assign_at_site_ten_raises(self):
    deck = BravoDeck()
    with self.assertRaises(ValueError):
      deck.assign_child_at_site(cor_96_wellplate_360uL_Fb(name="p1"), 10)

  def test_resource_at_out_of_range_site_raises(self):
    deck = BravoDeck()
    with self.assertRaises(ValueError):
      deck.resource_at_site(10)

  def test_unassign_out_of_range_site_raises(self):
    deck = BravoDeck()
    with self.assertRaises(ValueError):
      deck.unassign_site(10)


class LabwareForSiteTests(unittest.TestCase):
  """The internal Labware translation for whatever occupies a site."""

  def test_empty_site_has_no_labware(self):
    deck = BravoDeck()
    self.assertIsNone(deck.labware_for_site(6))

  def test_plate_translates_to_a_well_grid_labware(self):
    deck = BravoDeck()
    plate = cor_96_wellplate_360uL_Fb(name="my_plate")
    deck.assign_child_at_site(plate, 1)
    labware = deck.labware_for_site(1)
    assert labware is not None
    self.assertEqual(labware.name, "my_plate")
    self.assertEqual(labware.metadata["kind"], "plate")
    self.assertEqual(labware.metadata["base_class"], "plate")
    self.assertEqual(labware.metadata["rows"], 8)
    self.assertEqual(labware.metadata["cols"], 12)
    self.assertAlmostEqual(labware.metadata["spacing_x_mm"], plate.item_dx)
    self.assertAlmostEqual(labware.metadata["spacing_y_mm"], plate.item_dy)
    a1 = plate.get_item("A1")
    assert a1.location is not None
    self.assertAlmostEqual(labware.metadata["offset_x_mm"], a1.location.x)
    self.assertAlmostEqual(labware.metadata["offset_y_mm"], a1.location.y)
    self.assertEqual(labware.height, plate.get_size_z())
    self.assertEqual(labware.width, plate.get_size_y())
    self.assertEqual(labware.length, plate.get_size_x())
    self.assertEqual(labware.wells, 96)

  def test_tip_rack_translates_to_a_tip_box_labware(self):
    deck = BravoDeck()
    tip_rack = opentrons_96_tiprack_300ul(name="my_tips")
    deck.assign_child_at_site(tip_rack, 2)
    labware = deck.labware_for_site(2)
    assert labware is not None
    self.assertEqual(labware.metadata["kind"], "tip_box")
    self.assertEqual(labware.metadata["base_class"], "tip_box")
    self.assertEqual(labware.metadata["rows"], 8)
    self.assertEqual(labware.metadata["cols"], 12)

  def test_trash_translates_to_a_tip_trash_labware(self):
    # Bravo.tips_off's _require_tip_receptacle only accepts kind/base_class
    # in {"tip_box", "tip_trash"}; a Trash that fell through to the
    # generic "plate" case would make every drop-to-trash fail.
    deck = BravoDeck()
    trash = Trash(name="my_trash", size_x=127.0, size_y=85.0, size_z=10.0)
    deck.assign_child_at_site(trash, 3)
    labware = deck.labware_for_site(3)
    assert labware is not None
    self.assertEqual(labware.metadata["kind"], "tip_trash")
    self.assertEqual(labware.metadata["base_class"], "tip_trash")

  def test_labware_from_resource_matches_labware_for_site(self):
    deck = BravoDeck()
    plate = cor_96_wellplate_360uL_Fb(name="my_plate")
    deck.assign_child_at_site(plate, 1)
    direct = labware_from_resource(plate)
    via_site = deck.labware_for_site(1)
    assert via_site is not None
    self.assertEqual(direct.metadata, via_site.metadata)

  def test_pins_the_well_target_sign_convention_this_translator_assumes(self):
    """Not validated against hardware (see resource.py's _well_grid_metadata
    docstring): this pins the CURRENT sign convention exactly, so a future
    hardware check that finds it backwards fails this one test rather than
    surfacing as a silent, hard-to-trace liquid-handling geometry error.

    The convention this translator currently produces, combined with
    well_center_offset_from_teachpoint_mm, is:

        A1_target_xy == site_teachpoint_xy - item_a1.location

    not the sign-flipped alternative (site_teachpoint_xy + item_a1.location).
    """
    deck = BravoDeck()
    plate = cor_96_wellplate_360uL_Fb(name="my_plate")
    deck.assign_child_at_site(plate, 1)
    labware = deck.labware_for_site(1)
    assert labware is not None
    a1 = plate.get_item("A1")
    assert a1.location is not None
    teach_x = deck.teachpoints.get_teachpoint(1, "x")
    teach_y = deck.teachpoints.get_teachpoint(1, "y")
    offset_x, offset_y = well_center_offset_from_teachpoint_mm(labware.metadata, row=0, col=0)
    target_x, target_y = teach_x + offset_x, teach_y + offset_y
    self.assertAlmostEqual(target_x, teach_x - a1.location.x, places=6)
    self.assertAlmostEqual(target_y, teach_y - a1.location.y, places=6)
    # And explicitly NOT the sign-flipped alternative, so a reader who
    # flips the docstring's formula without updating this test notices.
    self.assertNotAlmostEqual(target_x, teach_x + a1.location.x, places=3)
    self.assertNotAlmostEqual(target_y, teach_y + a1.location.y, places=3)


if __name__ == "__main__":
  unittest.main()
