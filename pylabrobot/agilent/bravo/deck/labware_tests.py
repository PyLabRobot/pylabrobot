import unittest

from pylabrobot.agilent.bravo.deck.geometry import well_geometry_from_metadata
from pylabrobot.agilent.bravo.deck.labware import (
  DeckState,
  InMemoryLabwareCatalog,
  Labware,
  LabwareDefinition,
  LabwareStack,
  generated_lid_metadata,
  lid_gripper_offset_mm,
  lid_thickness_mm,
  synthesize_lid_labware,
)

_PLATE_384 = LabwareDefinition(
  id="builtin-384-greiner-781091",
  name="384 Greiner 781091 PS uclear",
  kind="sbs_plate",
  vendor="Greiner",
  catalog_number="781091",
  base_class="microplate",
  wells=384,
  length_mm=127.76,
  width_mm=85.48,
  height_mm=14.4,
  stack_height_mm=8.6,
  gripper_offset_mm=2.5,
  can_have_lid=True,
  lidded_height_mm=16.5,
  lidded_stack_height_mm=14.5,
  lid_resting_height_mm=9.5,
  lid_departure_height_mm=8.5,
  rows=16,
  cols=24,
  well_depth_mm=11.5,
  offset_x_mm=2.25,
  offset_y_mm=2.25,
  spacing_x_mm=4.5,
  spacing_y_mm=4.5,
  well_volume_ul=130.0,
  well_diameter_mm=3.3,
)

_PLATE_96 = LabwareDefinition(
  id="builtin-96-greiner-655101",
  name="96 Greiner 655101 PS Clr Rnd Well Flat Btm",
  kind="sbs_plate",
  vendor="Greiner",
  catalog_number="655101",
  base_class="microplate",
  wells=96,
  length_mm=127.76,
  width_mm=85.48,
  height_mm=14.4,
  stack_height_mm=8.6,
  gripper_offset_mm=0.5,
  can_have_lid=True,
  lidded_height_mm=16.5,
  lidded_stack_height_mm=14.5,
  lid_resting_height_mm=9.5,
  lid_departure_height_mm=8.5,
  rows=8,
  cols=12,
  spacing_x_mm=9.0,
  spacing_y_mm=9.0,
  well_volume_ul=300.0,
  well_diameter_mm=6.9,
)

_PLATE_SEALABLE = LabwareDefinition(
  id="sealable-plate",
  name="Sealable Plate",
  kind="sbs_plate",
  height_mm=14.4,
  stack_height_mm=8.6,
  can_be_sealed=True,
  sealed_height_mm=15.2,
  sealed_stacking_height_mm=9.0,
)


class LabwareDefinitionTests(unittest.TestCase):
  def test_to_summary_round_trips_construction_kwargs(self):
    summary = _PLATE_96.to_summary()
    self.assertEqual(summary["id"], "builtin-96-greiner-655101")
    self.assertEqual(summary["wells"], 96)
    self.assertEqual(summary["rows"], 8)
    self.assertEqual(summary["cols"], 12)

  def test_defaults_are_zero_or_empty(self):
    minimal = LabwareDefinition(id="x", name="X", kind="plate")
    self.assertEqual(minimal.height_mm, 0.0)
    self.assertEqual(minimal.supported_tip_ids, [])
    self.assertIsNone(minimal.lid_gripper_offset_mm)


class LabwareFromDefinitionTests(unittest.TestCase):
  def test_bare_plate_uses_base_height_and_stack_height(self):
    plate = Labware.from_definition(_PLATE_384)
    self.assertEqual(plate.height, 14.4)
    self.assertEqual(plate.stack_height, 8.6)
    self.assertFalse(plate.is_lidded)
    self.assertFalse(plate.is_sealed)

  def test_lidded_plate_uses_lidded_height_and_stack_height(self):
    plate = Labware.from_definition(_PLATE_384, is_lidded=True)
    self.assertEqual(plate.height, 16.5)
    self.assertEqual(plate.stack_height, 14.5)
    self.assertTrue(plate.is_lidded)
    self.assertIn("generated_lid", plate.metadata)

  def test_sealed_plate_uses_sealed_height_and_stack_height(self):
    plate = Labware.from_definition(_PLATE_SEALABLE, is_sealed=True)
    self.assertEqual(plate.height, 15.2)
    self.assertEqual(plate.stack_height, 9.0)

  def test_lidded_height_falls_back_to_base_height_when_unset(self):
    definition = LabwareDefinition(id="p", name="P", kind="plate", height_mm=10.0)
    plate = Labware.from_definition(definition, is_lidded=True)
    self.assertEqual(plate.height, 10.0)

  def test_metadata_carries_the_full_definition_and_derived_fields(self):
    plate = Labware.from_definition(_PLATE_384)
    self.assertEqual(plate.metadata["name"], _PLATE_384.name)
    self.assertEqual(plate.metadata["base_height_mm"], 14.4)
    self.assertEqual(plate.metadata["total_height_mm"], 14.4)

  def test_well_geometry_derived_from_metadata_matches_definition(self):
    plate = Labware.from_definition(_PLATE_384)
    geometry = well_geometry_from_metadata(plate.metadata)
    self.assertEqual((geometry.rows, geometry.cols), (16, 24))
    self.assertEqual((geometry.pitch_x_mm, geometry.pitch_y_mm), (4.5, 4.5))
    self.assertEqual((geometry.offset_x_mm, geometry.offset_y_mm), (2.25, 2.25))


class LidThicknessTests(unittest.TestCase):
  def test_uses_lidded_minus_resting_height_when_both_present(self):
    self.assertAlmostEqual(
      lid_thickness_mm({"lidded_height_mm": 16.5, "lid_resting_height_mm": 9.5}), 7.0
    )

  def test_uses_lidded_minus_base_height_when_no_resting_height(self):
    self.assertAlmostEqual(
      lid_thickness_mm({"lidded_height_mm": 16.5, "base_height_mm": 14.4}), 2.1
    )

  def test_uses_resting_height_alone_when_lidded_height_missing(self):
    self.assertAlmostEqual(lid_thickness_mm({"lid_resting_height_mm": 3.0}), 3.0)

  def test_floors_at_point_one_mm(self):
    self.assertEqual(lid_thickness_mm({}), 0.1)
    self.assertEqual(lid_thickness_mm(None), 0.1)


class LidGripperOffsetTests(unittest.TestCase):
  def test_explicit_lid_gripper_offset_wins(self):
    self.assertEqual(lid_gripper_offset_mm({"lid_gripper_offset_mm": 3.0}), 3.0)

  def test_robot_lid_gripper_offset_used_when_explicit_missing(self):
    self.assertEqual(lid_gripper_offset_mm({"robot_lid_gripper_offset_mm": 4.0}), 4.0)

  def test_fallback_used_when_no_metadata_offset(self):
    self.assertEqual(
      lid_gripper_offset_mm({"lid_resting_height_mm": 9.5}, fallback_gripper_offset_mm=1.0), 1.0
    )

  def test_fallback_is_clamped_to_lid_thickness(self):
    # lid thickness here is the 0.1 mm floor (no lidded/resting height given).
    offset = lid_gripper_offset_mm({}, fallback_gripper_offset_mm=5.0)
    self.assertEqual(offset, 0.1)


class GeneratedLidMetadataTests(unittest.TestCase):
  def test_valid_footprint_produces_lid_metadata(self):
    lid = generated_lid_metadata({"name": "Plate", "length_mm": 127.76, "width_mm": 85.48})
    self.assertIsNotNone(lid)
    assert lid is not None
    self.assertEqual(lid["name"], "Plate Lid")
    self.assertEqual(lid["kind"], "lid")
    self.assertEqual(lid["length_mm"], 127.76)
    self.assertEqual(lid["width_mm"], 85.48)

  def test_zero_footprint_returns_none(self):
    self.assertIsNone(generated_lid_metadata({"length_mm": 0.0, "width_mm": 85.48}))
    self.assertIsNone(generated_lid_metadata(None))


class SynthesizeLidLabwareTests(unittest.TestCase):
  def test_builds_a_lid_labware_from_a_plate(self):
    plate = Labware.from_definition(_PLATE_384)
    lid = synthesize_lid_labware(plate)
    self.assertEqual(lid.id, f"{plate.id}::lid")
    self.assertEqual(lid.definition_id, plate.definition_id)
    self.assertEqual(lid.labware_type, "lid")
    self.assertGreater(lid.height, 0.0)

  def test_raises_without_a_valid_footprint(self):
    plate = Labware(id="p", name="P", height=1.0, width=1.0, length=1.0, metadata={})
    with self.assertRaises(ValueError):
      synthesize_lid_labware(plate)


class InMemoryLabwareCatalogTests(unittest.TestCase):
  def test_list_definitions_returns_all_rows(self):
    catalog = InMemoryLabwareCatalog([_PLATE_384, _PLATE_96])
    self.assertEqual(len(catalog.list_definitions()), 2)

  def test_get_definition_by_id(self):
    catalog = InMemoryLabwareCatalog([_PLATE_384, _PLATE_96])
    self.assertIs(catalog.get_definition(_PLATE_96.id), _PLATE_96)

  def test_get_definition_unknown_id_returns_none(self):
    catalog = InMemoryLabwareCatalog([_PLATE_384])
    self.assertIsNone(catalog.get_definition("does-not-exist"))

  def test_alias_resolves_to_canonical_definition(self):
    catalog = InMemoryLabwareCatalog([_PLATE_384], aliases={"old-id": _PLATE_384.id})
    self.assertIs(catalog.get_definition("old-id"), _PLATE_384)

  def test_alias_colliding_with_a_real_id_is_ignored(self):
    catalog = InMemoryLabwareCatalog([_PLATE_384, _PLATE_96], aliases={_PLATE_96.id: _PLATE_384.id})
    self.assertIs(catalog.get_definition(_PLATE_96.id), _PLATE_96)


class LabwareStackHeightTests(unittest.TestCase):
  def test_empty_stack_height_is_zero(self):
    self.assertEqual(LabwareStack().get_total_height(), 0.0)

  def test_single_item_height_is_its_own_height(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    self.assertEqual(stack.get_total_height(), 14.4)

  def test_two_bare_plates_nest_using_stack_height_plus_top_height(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    stack.add(Labware.from_definition(_PLATE_384))
    # bottom contributes stack_height (8.6), top contributes full height (14.4)
    self.assertAlmostEqual(stack.get_total_height(), 8.6 + 14.4)

  def test_three_plate_stack_nests_all_but_the_top(self):
    stack = LabwareStack()
    for _ in range(3):
      stack.add(Labware.from_definition(_PLATE_384))
    self.assertAlmostEqual(stack.get_total_height(), 8.6 + 8.6 + 14.4)

  def test_lidded_top_plate_adds_full_lidded_height(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    stack.add(Labware.from_definition(_PLATE_384, is_lidded=True))
    self.assertAlmostEqual(stack.get_total_height(), 8.6 + 16.5)

  def test_get_location_height_excludes_the_top_plates_own_height(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    stack.add(Labware.from_definition(_PLATE_384))
    self.assertAlmostEqual(stack.get_location_height(), 8.6)

  def test_get_location_height_on_empty_stack_is_zero(self):
    self.assertEqual(LabwareStack().get_location_height(), 0.0)

  def test_get_stacking_height_sums_every_items_stack_height(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    stack.add(Labware.from_definition(_PLATE_384))
    self.assertAlmostEqual(stack.get_stacking_height(), 8.6 + 8.6)

  def test_falls_back_to_full_height_when_stack_height_is_zero(self):
    definition = LabwareDefinition(id="p", name="P", kind="plate", height_mm=5.0)
    stack = LabwareStack()
    stack.add(Labware.from_definition(definition))
    stack.add(Labware.from_definition(definition))
    self.assertAlmostEqual(stack.get_total_height(), 5.0 + 5.0)


class LabwareStackOperationsTests(unittest.TestCase):
  def test_add_then_top_returns_the_last_added_item(self):
    stack = LabwareStack()
    a = Labware.from_definition(_PLATE_384)
    b = Labware.from_definition(_PLATE_96)
    stack.add(a)
    stack.add(b)
    self.assertIs(stack.top, b)

  def test_remove_top_pops_and_returns_the_top_item(self):
    stack = LabwareStack()
    a = Labware.from_definition(_PLATE_384)
    b = Labware.from_definition(_PLATE_96)
    stack.add(a)
    stack.add(b)
    self.assertIs(stack.remove_top(), b)
    self.assertIs(stack.top, a)

  def test_remove_top_on_empty_stack_raises(self):
    with self.assertRaises(IndexError):
      LabwareStack().remove_top()

  def test_replace_discards_the_previous_stack(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    stack.add(Labware.from_definition(_PLATE_384))
    replacement = Labware.from_definition(_PLATE_96)
    stack.replace(replacement)
    self.assertEqual(len(stack), 1)
    self.assertIs(stack.top, replacement)

  def test_bool_and_len_reflect_contents(self):
    stack = LabwareStack()
    self.assertFalse(stack)
    self.assertEqual(len(stack), 0)
    stack.add(Labware.from_definition(_PLATE_384))
    self.assertTrue(stack)
    self.assertEqual(len(stack), 1)

  def test_items_returns_a_bottom_first_copy(self):
    stack = LabwareStack()
    a = Labware.from_definition(_PLATE_384)
    b = Labware.from_definition(_PLATE_96)
    stack.add(a)
    stack.add(b)
    items = stack.items
    self.assertEqual(items, [a, b])
    items.append(Labware.from_definition(_PLATE_384))
    self.assertEqual(len(stack), 2)  # the copy's mutation did not leak in


class MountedGroupTests(unittest.TestCase):
  def test_unmounted_top_group_is_just_the_top_item(self):
    stack = LabwareStack()
    bottom = Labware.from_definition(_PLATE_384)
    top = Labware.from_definition(_PLATE_96)
    stack.add(bottom)
    stack.add(top)
    self.assertEqual(stack.mounted_group_from_top(), [top])

  def test_mounted_pair_travels_together(self):
    stack = LabwareStack()
    bottom = Labware.from_definition(_PLATE_384)
    top = Labware.from_definition(_PLATE_96)
    top.is_mounted = True
    stack.add(bottom)
    stack.add(top)
    self.assertEqual(stack.mounted_group_from_top(), [top, bottom])

  def test_empty_stack_group_is_empty(self):
    self.assertEqual(LabwareStack().mounted_group_from_top(), [])

  def test_support_height_below_group_for_unmounted_top(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    stack.add(Labware.from_definition(_PLATE_96))
    self.assertAlmostEqual(stack.get_support_height_below_group(), stack.get_location_height())

  def test_support_height_below_mounted_pair_skips_the_group_itself(self):
    stack = LabwareStack()
    base = Labware.from_definition(_PLATE_384)
    filter_plate = Labware.from_definition(_PLATE_96)
    collection_plate = Labware.from_definition(_PLATE_96)
    filter_plate.is_mounted = True
    stack.add(base)
    stack.add(collection_plate)
    stack.add(filter_plate)
    # group = [filter_plate, collection_plate]; only `base` remains as support.
    self.assertAlmostEqual(stack.get_support_height_below_group(), base.stack_height or base.height)

  def test_support_height_below_group_on_bare_deck_is_zero(self):
    stack = LabwareStack()
    stack.add(Labware.from_definition(_PLATE_384))
    self.assertEqual(stack.get_support_height_below_group(), 0.0)


class DeckStateTests(unittest.TestCase):
  def test_new_deck_has_nine_empty_stacks(self):
    deck = DeckState()
    self.assertEqual(deck.get_all_heights(), {loc: 0.0 for loc in range(1, 10)})

  def test_add_then_get_stack_returns_the_populated_stack(self):
    deck = DeckState()
    plate = Labware.from_definition(_PLATE_384)
    deck.add(3, plate)
    self.assertIs(deck.get_stack(3).top, plate)

  def test_remove_pops_the_top_item(self):
    deck = DeckState()
    plate = Labware.from_definition(_PLATE_384)
    deck.add(3, plate)
    self.assertIs(deck.remove(3), plate)
    self.assertEqual(deck.get_height(3), 0.0)

  def test_set_single_replaces_the_whole_stack(self):
    deck = DeckState()
    deck.add(3, Labware.from_definition(_PLATE_384))
    deck.add(3, Labware.from_definition(_PLATE_384))
    replacement = Labware.from_definition(_PLATE_96)
    deck.set_single(3, replacement)
    self.assertEqual(len(deck.get_stack(3)), 1)
    self.assertIs(deck.get_stack(3).top, replacement)

  def test_get_height_reflects_stack_arithmetic(self):
    deck = DeckState()
    deck.add(5, Labware.from_definition(_PLATE_384))
    deck.add(5, Labware.from_definition(_PLATE_384))
    self.assertAlmostEqual(deck.get_height(5), 8.6 + 14.4)

  def test_get_location_height_and_get_stacking_height_delegate_to_the_stack(self):
    deck = DeckState()
    deck.add(5, Labware.from_definition(_PLATE_384))
    deck.add(5, Labware.from_definition(_PLATE_384))
    self.assertAlmostEqual(deck.get_location_height(5), 8.6)
    self.assertAlmostEqual(deck.get_stacking_height(5), 8.6 + 8.6)

  def test_clear_empties_a_single_location(self):
    deck = DeckState()
    deck.add(1, Labware.from_definition(_PLATE_384))
    deck.add(2, Labware.from_definition(_PLATE_384))
    deck.clear(1)
    self.assertEqual(deck.get_height(1), 0.0)
    self.assertGreater(deck.get_height(2), 0.0)

  def test_clear_all_empties_every_location(self):
    deck = DeckState()
    for loc in range(1, 10):
      deck.add(loc, Labware.from_definition(_PLATE_384))
    deck.clear_all()
    self.assertEqual(deck.get_all_heights(), {loc: 0.0 for loc in range(1, 10)})

  def test_remove_mounted_group_and_add_mounted_group_round_trip(self):
    source = DeckState()
    dest = DeckState()
    base = Labware.from_definition(_PLATE_384)
    top = Labware.from_definition(_PLATE_96)
    top.is_mounted = True
    source.add(1, base)
    source.add(1, top)
    group = source.remove_mounted_group(1)
    self.assertEqual(group, [top, base])
    self.assertEqual(len(source.get_stack(1)), 0)
    dest.add_mounted_group(2, group)
    self.assertEqual(dest.get_stack(2).items, [base, top])

  def test_out_of_range_location_is_rejected_by_every_accessor(self):
    deck = DeckState()
    plate = Labware.from_definition(_PLATE_384)
    for bad_location in (0, 10, -1):
      with self.subTest(location=bad_location):
        with self.assertRaises(ValueError):
          deck.add(bad_location, plate)
        with self.assertRaises(ValueError):
          deck.remove(bad_location)
        with self.assertRaises(ValueError):
          deck.get_stack(bad_location)
        with self.assertRaises(ValueError):
          deck.get_height(bad_location)
        with self.assertRaises(ValueError):
          deck.clear(bad_location)


if __name__ == "__main__":
  unittest.main()
