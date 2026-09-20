import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import HamiltonTip, hamilton_tip_300uL
from pylabrobot.resources.hamilton.tip_creators import TIP_DIAMETER, TipSize
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot


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
