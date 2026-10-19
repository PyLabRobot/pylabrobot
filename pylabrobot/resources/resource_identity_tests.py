"""Identity remains stable when descriptive metadata and geometry change."""

import json
from uuid import UUID

import pytest

from pylabrobot.resources import (
  Coordinate,
  Cor_96_wellplate_360ul_Fb,
  Deck,
  Eppendorf_96_wellplate_250ul_Vb,
  Greiner_384_wellplate_28ul_Fb,
  Plate,
  Resource,
  TipRack,
  Well,
  cor_96_wellplate_360uL_Fb,
  hamilton_96_tiprack_300uL_filter,
)
from pylabrobot.resources.errors import ResourceNotFoundError
from pylabrobot.resources.opentrons import opentrons_96_filtertiprack_20ul
from pylabrobot.serializer import deserialize


def resource(name="sample", identifier=None):
  return Resource(name, 1, 2, 3, identifier=identifier)


def test_default_and_custom_identifiers():
  first, second = resource(), resource()
  assert UUID(first.identifier).version == 4
  assert first.identifier != second.identifier
  assert resource(identifier="user/defined id").identifier == "user/defined id"
  with pytest.raises(AttributeError):
    first.identifier = "changed"
  for invalid in ("", 5, False):
    with pytest.raises(ValueError):
      resource(identifier=invalid)


@pytest.mark.parametrize(
  "factory",
  [cor_96_wellplate_360uL_Fb, Eppendorf_96_wellplate_250ul_Vb, Greiner_384_wellplate_28ul_Fb],
)
def test_plate_definitions_accept_custom_identifiers_and_default_to_uuids(factory):
  plate = factory("sample", identifier="user/defined plate")
  assert plate.identifier == "user/defined plate"
  assert plate.name == "sample"
  first, second = factory("sample"), factory("sample")
  assert UUID(first.identifier).version == 4
  assert UUID(second.identifier).version == 4
  assert first.identifier != second.identifier


def test_name_metadata_and_hash_are_independent_of_identity():
  deck = Deck(100, 100, 100)
  first, second = resource(), resource()
  deck.assign_child_resource(first, Coordinate.zero())
  deck.assign_child_resource(second, Coordinate(5, 0, 0))
  resources = {first, second}
  first.name = "renamed"
  assert first.metadata["name"] == "renamed"
  second.metadata["name"] = "renamed"
  assert second.name == "renamed"
  assert first in resources and second in resources
  assert deck.get_resource_by_identifier(first.identifier) is first
  with pytest.raises(ValueError, match="ambiguous"):
    deck.get_resource("renamed")
  first.unassign()
  assert deck.get_resource("renamed") is second
  assert not deck.has_resource_by_identifier(first.identifier)
  deck.clear()
  assert not deck.children


def test_root_identifier_is_indexed_and_name_can_be_ambiguous():
  deck = Deck(100, 100, 100, identifier="root")
  assert deck.get_resource_by_identifier("root") is deck
  with pytest.raises(ValueError):
    deck.assign_child_resource(resource(identifier="root"), Coordinate.zero())
  child = resource(name=deck.name)
  deck.assign_child_resource(child, Coordinate.zero())
  with pytest.raises(ValueError, match="ambiguous"):
    deck.get_resource(deck.name)


@pytest.mark.parametrize("use_deck", [True, False])
def test_subtree_conflicts_are_checked_before_mutation(use_deck):
  root = Deck(100, 100, 100) if use_deck else resource("root")
  existing = resource(identifier="collision")
  root.assign_child_resource(existing, Coordinate.zero())
  source = resource("source")
  incoming = resource("incoming")
  incoming.assign_child_resource(resource("different", "collision"), Coordinate.zero())
  source.assign_child_resource(incoming, Coordinate.zero())
  with pytest.raises(ValueError, match="collision"):
    root.assign_child_resource(incoming, Coordinate.zero())
  assert incoming.parent is source
  assert root.get_resource_by_identifier("collision") is existing
  assert root.children == [existing]


def test_reparenting_same_instance_preserves_index():
  deck = Deck(100, 100, 100)
  first, second, child = resource(), resource(), resource()
  deck.assign_child_resource(first, Coordinate.zero())
  deck.assign_child_resource(second, Coordinate(5, 0, 0))
  first.assign_child_resource(child, Coordinate.zero())
  second.assign_child_resource(child, Coordinate.zero())
  assert child.parent is second
  assert first.children == []
  assert deck.get_resource_by_identifier(child.identifier) is child
  with pytest.raises(ValueError):
    first.unassign_child_resource(child.named("same identity, different instance"))


@pytest.mark.parametrize(
  "transform",
  [
    lambda r: r.named("renamed"),
    lambda r: r.at(Coordinate(5, 6, 7)),
    lambda r: r.rotated(z=90),
  ],
)
def test_transforms_preserve_subtree_identity_and_state(transform):
  plate = Cor_96_wellplate_360ul_Fb("plate", identifier="plate-id")
  plate.get_item("A1").tracker.set_volume(25)
  plate.metadata["experiment"] = {"phase": "initial"}
  transformed = transform(plate)
  assert transformed is not plate
  assert transformed == plate
  assert [w.identifier for w in transformed.children] == [w.identifier for w in plate.children]
  assert transformed.get_item("A1").tracker.get_used_volume() == 25
  assert transformed.get_item("A1").get_identifier() == "A1"
  transformed.metadata["experiment"]["phase"] = "changed"
  assert plate.metadata["experiment"]["phase"] == "initial"


def test_copy_preserves_subtree_identity_and_references():
  deck = Deck(500, 500, 500)
  plate = Cor_96_wellplate_360ul_Fb("plate")
  plate.get_item("A1").tracker.set_volume(25)
  deck.assign_child_resource(plate, Coordinate.zero())
  plate.metadata["references"] = {"identifier": plate.identifier, "name": plate.identifier}
  cloned = deck.copy()
  assert cloned is not deck
  originals = {r.identifier for r in [deck, *deck.get_all_children()]}
  copies = {r.identifier for r in [cloned, *cloned.get_all_children()]}
  assert originals == copies
  cloned_plate = cloned.get_resource("plate")
  assert isinstance(cloned_plate, Plate)
  assert cloned_plate is not plate
  assert cloned_plate.metadata == plate.metadata
  well = cloned_plate.get_item("A1")
  assert cloned.get_resource_by_identifier(well.identifier) is well
  assert cloned_plate.get_child_identifier(well) == "A1"
  assert well.tracker.get_used_volume() == 25
  assert well.parent is cloned_plate
  well.tracker.set_volume(0)
  assert plate.get_item("A1").tracker.get_used_volume() == 25


def test_roundtrip_preserves_identity_metadata_and_duplicate_name_state():
  deck = Deck(500, 500, 500)
  plates = [
    Plate("plate", 10, 20, 30, ordered_items={"A1": Well("sample", 4, 4, 10).at(Coordinate.zero())})
    for _ in range(2)
  ]
  for i, plate in enumerate(plates):
    deck.assign_child_resource(plate, Coordinate(150 * i, 0, 0))
    plate.get_item("A1").tracker.set_volume(10 + i)
  deck.metadata["type"] = "ordinary metadata, not a serialized Python type"
  deck.metadata["note"] = "Infinity"
  serialized = json.loads(json.dumps(deck.serialize()))
  restored = deserialize(serialized, allow_marshal=True)
  restored.load_all_state(json.loads(json.dumps(deck.serialize_all_state())))
  assert restored.serialize() == serialized
  for i, plate in enumerate(plates):
    well = restored.get_resource_by_identifier(plate.get_item("A1").identifier)
    assert isinstance(well, Well)
    assert well.tracker.get_used_volume() == 10 + i
  with pytest.raises(ValueError, match="ambiguous"):
    restored.load_all_state({plates[0].name: {}})


def test_legacy_nested_layout_and_state_migrate_to_identifiers():
  deck = Deck(500, 500, 500)
  plate = Cor_96_wellplate_360ul_Fb("plate")
  deck.assign_child_resource(plate, Coordinate.zero())
  layout = deck.serialize()
  names = {r.identifier: r.name for r in [deck, *deck.get_all_children()]}

  def legacy(data):
    data["name"] = data.pop("metadata")["name"]
    data.pop("identifier")
    data["parent_name"] = names.get(data.pop("parent_identifier", None))
    if "ordering" in data:
      data["ordering"] = {address: names[ident] for address, ident in data["ordering"].items()}
    for child in data.get("children", []):
      legacy(child)

  legacy(layout)
  restored = Resource.deserialize(layout, allow_marshal=True)
  restored_plate = restored.get_resource("plate")
  assert isinstance(restored_plate, Plate)
  well = restored_plate.get_item("A1")
  assert restored_plate.get_child_identifier(well) == "A1"
  assert UUID(well.identifier).version == 4
  well.tracker.set_volume(17)
  legacy_state = {well.name: well.serialize_state()}
  well.tracker.set_volume(0)
  restored.load_all_state(legacy_state)
  assert well.tracker.get_used_volume() == 17
  assert "identifier" not in layout  # Loading does not modify the caller's data.


@pytest.mark.parametrize(
  "factory", [hamilton_96_tiprack_300uL_filter, opentrons_96_filtertiprack_20ul]
)
def test_tip_rack_factory_and_serialization_preserve_identity(factory):
  rack = factory("tips", identifier="rack-id")
  rack.metadata["lot"] = "42"
  restored = Resource.deserialize(rack.serialize())
  assert isinstance(restored, TipRack)
  assert restored.identifier == "rack-id"
  assert restored.metadata == {"name": "tips", "lot": "42"}
  assert restored.get_item("A1").identifier == rack.get_item("A1").identifier
  assert restored.get_item("A1").get_identifier() == "A1"
  with pytest.raises(ResourceNotFoundError):
    restored.get_resource_by_identifier("missing")


def test_grid_addresses_resolve_identifiers_after_renaming_and_reordering():
  plate = Cor_96_wellplate_360ul_Fb("plate")
  a1, b1 = plate.get_item("A1"), plate.get_item("B1")
  a1.name = b1.name = "same"
  data = plate.serialize()
  data["children"].reverse()
  restored = Plate.deserialize(data)
  assert restored.get_item("A1").identifier == a1.identifier
  assert restored.get_item(1).identifier == b1.identifier
  assert restored.get_item("A1").get_identifier() == "A1"


def test_legacy_state_with_identifier_equal_to_name():
  root = Resource("root", 10, 10, 10, identifier="root")
  well = Well("well", 4, 4, 10)
  root.assign_child_resource(well, Coordinate.zero())
  well.tracker.set_volume(12)
  state = well.serialize_state()
  well.tracker.set_volume(0)
  root.load_all_state({"root": {}, "well": state})
  assert well.tracker.get_used_volume() == 12


def test_builtin_trash_roles_survive_renaming_and_roundtrip():
  from pylabrobot.resources import STARLetDeck
  from pylabrobot.resources.opentrons import OTDeck

  for deck in [STARLetDeck(), OTDeck()]:
    trash = deck.get_trash_area()
    trash.name = "renamed"
    restored = Resource.deserialize(deck.serialize())
    assert isinstance(restored, Deck)
    assert restored.get_trash_area().identifier == trash.identifier
    assert restored.get_trash_area().name == "renamed"
  star = STARLetDeck()
  trash96 = star.get_trash_area96()
  trash96.name = "renamed"
  transformed = star.named("new deck label")
  assert transformed.get_trash_area96().identifier == trash96.identifier


def test_random_tip_sampling_distinguishes_duplicate_names():
  import asyncio

  from pylabrobot.resources.functional import randomized_tip_spot_generator

  first = hamilton_96_tiprack_300uL_filter("same").get_item("A1")
  second = hamilton_96_tiprack_300uL_filter("same").get_item("A1")
  generator = randomized_tip_spot_generator([first, second], K=1)
  sampled = asyncio.run(generator.get(2))
  assert sampled[0].identifier != sampled[1].identifier


def test_copy_preserves_tool_state_and_tip_origins():
  from pylabrobot.resources import PrepDeck
  from pylabrobot.resources.tip_rack import tip_origin

  deck = PrepDeck()
  holder = deck.core_gripper_holder
  assert holder is not None
  front = holder.front_tool
  front.name = "label"
  rack = hamilton_96_tiprack_300uL_filter("same")
  deck[0] = rack
  spot = rack.get_item("A1")
  tip = spot.get_tip()
  spot.name = tip.name = "label"
  tip.tracker.set_volume(23)
  cloned = deck.copy()
  cloned_holder = cloned.core_gripper_holder
  assert cloned_holder is not None
  assert cloned_holder.front_tool.name == "label"
  assert cloned_holder.front_tool.identifier == front.identifier
  cloned_rack = cloned[0].resource
  assert isinstance(cloned_rack, TipRack)
  cloned_spot = cloned_rack.get_item("A1")
  cloned_tip = cloned_spot.get_tip()
  assert cloned_tip is not tip
  assert cloned_tip.identifier == tip.identifier
  assert tip_origin(cloned_tip, cloned) is cloned_spot
  assert cloned_tip.tracker.get_used_volume() == 23


def test_prep_roles_and_slots_survive_duplicate_labels():
  from pylabrobot.resources import PrepDeck

  deck = PrepDeck()
  slot_ids = [spot.identifier for spot in deck.spots]
  waste_ids = {role: resource.identifier for role, resource in deck.waste_positions.items()}
  for child in deck.get_all_children():
    child.name = "same"
  restored = PrepDeck.deserialize(deck.serialize())
  assert [spot.identifier for spot in restored.spots] == slot_ids
  assert {
    role: resource.identifier for role, resource in restored.waste_positions.items()
  } == waste_ids


def test_opentrons_slots_survive_renaming_and_reordered_serialization():
  from pylabrobot.resources.opentrons import OTDeck
  from pylabrobot.resources.opentrons.flex_deck import FlexDeck

  for deck, slot in [(OTDeck(with_trash=False), 1), (FlexDeck(), "A1")]:
    plate = Cor_96_wellplate_360ul_Fb("plate")
    if isinstance(deck, OTDeck):
      deck.assign_child_at_slot(plate, 1)
    else:
      assert isinstance(deck, FlexDeck)
      deck.assign_child_at_slot(plate, "A1")
    for child in deck.children:
      child.name = "same"
    data = deck.serialize()
    data["children"].reverse()
    restored = Resource.deserialize(data)
    assert isinstance(restored, (OTDeck, FlexDeck))
    assert restored.get_slot(restored.get_resource_by_identifier(plate.identifier)) == slot
    cloned = restored.copy()
    assert cloned.get_slot(cloned.get_resource("plate")) == slot
