import os
import tempfile
import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton import (
  HamiltonTip,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_tip_300uL,
)
from pylabrobot.resources.hamilton.tip_creators import TIP_DIAMETER, TipSize
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import NestedTipRack, TipRack, TipSpot


class SimpleTipRack(TipRack):
  """Minimal concrete TipRack for testing."""

  def __init__(self, name: str):
    spot = TipSpot(
      name="A1",
      size_x=1.0,
      size_y=1.0,
      make_tip=lambda name: Tip(
        name=name,
        has_filter=False,
        maximal_volume=10.0,
        fitting_depth=1.0,
        diameter=TIP_DIAMETER[TipSize.STANDARD_VOLUME],
        size_z=10.0,
      ),
    )
    spot.location = Coordinate(0.0, 0.0, 0.0)
    ordered_items = {"A1": spot}
    super().__init__(
      name=name,
      size_x=1.0,
      size_y=1.0,
      size_z=1.0,
      ordered_items=ordered_items,
    )


class TipRackNamingTests(unittest.TestCase):
  """Tests for tip naming behavior in TipSpot/TipRack."""

  def test_get_tip_assigns_unique_names(self):
    rack = SimpleTipRack("my_rack")
    spot = rack.get_item("A1")

    tip1 = spot.make_tip()
    tip2 = spot.make_tip()

    self.assertIsNotNone(tip1.name)
    self.assertIsNotNone(tip2.name)
    self.assertNotEqual(tip1.name, tip2.name)

  def test_set_tip_state_fills_with_named_tips(self):
    rack = SimpleTipRack("my_rack")

    rack.set_tip_state({"A1": True})

    spot = rack.get_item("A1")
    tip = spot.tracker.get_tip()
    self.assertIsNotNone(tip.name)

  def test_deserialize_prototype_with_resource_state(self):
    """A restored spot constructs named tips from a resource prototype."""
    spot = TipSpot("spot", 9, 9, make_tip=hamilton_tip_300uL)
    data = spot.serialize()
    data["prototype_tip"]["rotation"] = {"type": "Rotation", "x": 0, "y": 0, "z": 90}
    data["prototype_tip"]["metadata"] = {"batch": "example"}
    data["prototype_tip"]["location"] = Coordinate(1, 2, 3).serialize()

    restored = TipSpot.deserialize(data)
    first, second = restored.make_tip(), restored.make_tip()
    self.assertIsInstance(first, HamiltonTip)
    self.assertNotEqual(first.name, second.name)
    self.assertEqual(first.rotation.z, 90)
    self.assertEqual(first.metadata, {"batch": "example"})
    self.assertIsNone(first.location)


class NestedTipRackTests(unittest.TestCase):
  """Tests for NestedTipRack."""

  def test_deserialize_round_trip(self):
    rack = hamilton_96_tiprack_50uL_NTR("rack")

    restored = NestedTipRack.deserialize(rack.serialize())

    self.assertEqual(restored.stacking_z_height, rack.stacking_z_height)
    self.assertEqual(restored.model, rack.model)
    self.assertEqual(restored.num_items, rack.num_items)

  def test_copy_stacks_like_original(self):
    rack = hamilton_96_tiprack_50uL_NTR("rack")
    copied = rack.copy()

    rack.assign_child_resource(hamilton_96_tiprack_50uL_NTR("stacked_on_rack"))
    copied.assign_child_resource(hamilton_96_tiprack_50uL_NTR("stacked_on_copy"))

    self.assertEqual(copied.stacking_z_height, rack.stacking_z_height)
    self.assertEqual(
      copied.get_resource("stacked_on_copy").location,
      rack.get_resource("stacked_on_rack").location,
    )

  def test_save_and_load_deck(self):
    deck = Deck(size_x=1000, size_y=1000, size_z=1000)
    rack = hamilton_96_tiprack_50uL_NTR("rack")
    deck.assign_child_resource(rack, location=Coordinate(100, 100, 0))

    with tempfile.TemporaryDirectory() as tmp_dir:
      fn = os.path.join(tmp_dir, "deck.json")
      deck.save(fn)
      loaded = Deck.load_from_json_file(fn)

    loaded_rack = loaded.get_resource("rack")
    assert isinstance(loaded_rack, NestedTipRack)
    self.assertEqual(loaded_rack.stacking_z_height, rack.stacking_z_height)
    self.assertEqual(loaded_rack.location, rack.location)
