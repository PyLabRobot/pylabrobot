"""The flattener: what it emits, and that reusing derived models changes nothing about it."""

import json
import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.hamilton import hamilton_96_tiprack_1000uL
from pylabrobot.resources.resource import Resource
from pylabrobot.visualizer3D.facility import Facility
from pylabrobot.visualizer3D.scene import (
  Scene,
  _model_of,
  build_scene,
  pack_state,
  state_signature,
)


def canonical(scene: Scene) -> str:
  """The scene as it is sent, in a key order that lets two of them be compared byte for byte."""
  return json.dumps(scene.serialize(), sort_keys=True)


def facility_with(plates: int) -> Facility:
  """A facility holding `plates` identical plates, side by side."""
  facility = Facility(name="facility", size_x=10_000, size_y=10_000, size_z=500)
  for i in range(plates):
    facility.assign_child_resource(
      cor_96_wellplate_360uL_Fb(name=f"plate_{i}"), location=Coordinate(150 * i, 0, 0)
    )
  return facility


def facility_with_a_full_rack() -> Facility:
  """A facility holding a tip rack full of tips: a tip spot serializes without its tip."""
  facility = Facility(name="facility", size_x=1000, size_y=1000, size_z=500)
  facility.assign_child_resource(
    hamilton_96_tiprack_1000uL(name="rack", with_tips=True), location=Coordinate(100, 100, 0)
  )
  return facility


class SceneTests(unittest.TestCase):
  def test_emits_parents_before_children(self):
    """A client resolves world transforms in one forward pass, which only works in this order."""
    scene = build_scene(facility_with(2))
    for index, parent in enumerate(scene.parent_of_instance):
      self.assertLess(parent, index, f"instance {index} is emitted before its parent")

  def test_identical_resources_share_one_model(self):
    """The whole point of the split: geometry is sent once, however many stand on it."""
    one = build_scene(facility_with(1))
    many = build_scene(facility_with(20))
    self.assertEqual(len(many.models), len(one.models))
    self.assertEqual(len(many.names), 1 + 20 * 97)  # facility, then plate and its 96 wells

  def test_a_move_is_seen_through_reused_models(self):
    """Skipping the derive must not change a byte of what is sent: a model is reused, a position
    never is, so moving something must still show up."""
    facility = facility_with(3)
    first = build_scene(facility)
    names = frozenset(first.names)
    facility.get_resource("plate_0").location = Coordinate(999, 888, 0)
    warm = build_scene(facility, known=first.derived, known_names=names)
    self.assertEqual(canonical(warm), canonical(build_scene(facility)))

  def test_a_racked_tip_hangs_from_its_collar(self):
    """A tip serializes without its location; the scene places it where its spot put it."""
    facility = Facility(name="facility", size_x=1000, size_y=1000, size_z=500)
    rack = hamilton_96_tiprack_1000uL(name="rack", with_tips=True)
    facility.assign_child_resource(rack, location=Coordinate(100, 100, 0))
    tip = rack.get_item("A1").get_tip()
    location = tip.location
    assert location is not None

    scene = build_scene(facility)
    index = scene.names.index(tip.name)
    self.assertEqual(
      tuple(scene.transforms[6 * index : 6 * index + 3]), (location.x, location.y, location.z)
    )

  def test_models_are_not_reused_when_the_names_change(self):
    """Whether a string counts as a reference depends on which names exist, so a changed tree
    invalidates what was derived from the old one."""
    facility = facility_with(2)
    first = build_scene(facility)
    names = frozenset(first.names)
    # A plate's ordering links its wells by name: with A1 out of the tree, that entry is a name.
    plate = facility.get_resource("plate_0")
    plate.unassign_child_resource(facility.get_resource("plate_0_well_A1"))
    warm = build_scene(facility, known=first.derived, known_names=names)
    self.assertNotEqual(
      warm.derived["plate_0"], first.derived["plate_0"], "the model kept its link"
    )
    self.assertEqual(canonical(warm), canonical(build_scene(facility)))

  def test_a_discard_reuses_the_models_that_do_not_link_to_it(self):
    """Any change of names threw every derived model away, so an assign or a discard cost the
    cold build. A model that holds no link is what it was, whichever names exist."""
    facility = facility_with(2)
    box = Resource(name="box", size_x=10, size_y=10, size_z=10)
    facility.assign_child_resource(box, location=Coordinate(900, 900, 0))
    first = build_scene(facility)
    names = frozenset(first.names)
    facility.unassign_child_resource(box)
    warm = build_scene(facility, known=first.derived, known_names=names)
    self.assertIs(warm.derived["plate_0_well_A1"], first.derived["plate_0_well_A1"])
    self.assertIsNot(warm.derived["plate_0"], first.derived["plate_0"])  # links its wells
    self.assertEqual(
      json.dumps(warm.serialize(), sort_keys=True),
      json.dumps(build_scene(facility).serialize(), sort_keys=True),
    )

  def test_a_name_that_reappears_is_a_link_again(self):
    """A model derived while a name was gone holds it as a plain string; put back, it is a link."""
    facility = facility_with(1)
    plate = facility.get_resource("plate_0")
    well = facility.get_resource("plate_0_well_A1")
    location = well.location
    plate.unassign_child_resource(well)
    first = build_scene(facility)
    names = frozenset(first.names)
    self.assertEqual(first.derived["plate_0"]["ordering"]["A1"], "plate_0_well_A1")
    plate.assign_child_resource(well, location=location)
    warm = build_scene(facility, known=first.derived, known_names=names)
    self.assertEqual(
      json.dumps(warm.serialize(), sort_keys=True),
      json.dumps(build_scene(facility).serialize(), sort_keys=True),
    )

  def test_a_model_is_the_resources_own_whether_or_not_its_parent_serialized_it(self):
    """A parent's serialization feeds its children's models, and a child it left out serializes
    itself: either way the model is what the resource itself says it is."""
    facility = facility_with_a_full_rack()
    facility.assign_child_resource(
      cor_96_wellplate_360uL_Fb(name="plate"), location=Coordinate(300, 100, 0)
    )
    scene = build_scene(facility)
    resources: list = []

    def gather(resource: Resource) -> None:
      resources.append(resource)
      for child in resource.children:
        gather(child)

    gather(facility)
    self.assertEqual(scene.names, [resource.name for resource in resources])
    names = frozenset(scene.names)
    for resource, model_index in zip(resources, scene.model_of_instance):
      expected = _model_of(resource.serialize(), names)
      model = scene.models[model_index]
      self.assertEqual({k: model[k] for k in expected}, expected, resource.name)

  def test_the_legacy_size_counts_one_node_per_resource(self):
    """The comparison lists every resource as a node, including the tip its spot serializes
    without, so a serialization that hands children down must not lose the ones a parent drops."""
    facility = facility_with_a_full_rack()
    scene = build_scene(facility, measure_legacy=True)

    def one_node_each(resource: Resource) -> dict:
      data = resource.serialize()
      data["children"] = [one_node_each(child) for child in resource.children]
      return data

    self.assertEqual(scene.legacy_bytes, len(json.dumps(one_node_each(facility))))


class PackStateTests(unittest.TestCase):
  def test_resources_that_differ_only_in_position_share_one_state(self):
    """A position rode inside the state, so two wells at the same volume were two states, and a
    plate of them that had moved once could never share anything again."""
    at = lambda x: {"x": x, "y": 0.0, "z": 0.0, "type": "Coordinate"}  # noqa: E731
    packed = pack_state(
      {
        "a": state_signature({"volume": 100.0, "location": at(1.0)}),
        "b": state_signature({"volume": 100.0, "location": at(2.0)}),
        "c": state_signature({"volume": 100.0}),
      }
    )
    self.assertEqual(len(packed["states"]), 1)
    self.assertEqual(packed["of"], {"a": 0, "b": 0, "c": 0})
    self.assertEqual(packed["locations"], {"a": at(1.0), "b": at(2.0)})
    self.assertNotIn("location", packed["states"][0])


if __name__ == "__main__":
  unittest.main()
