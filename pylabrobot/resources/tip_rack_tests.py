import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot


class SimpleTipRack(TipRack):
  """Minimal concrete TipRack for testing."""

  def __init__(self, name: str):
    spot = TipSpot(
      name="A1",
      size_x=1.0,
      size_y=1.0,
      make_tip=lambda name: Tip(False, 10.0, 10.0, 1.0, name=name),
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


class TipSpotSizeTests(unittest.TestCase):
  """Tests that a tip spot keeps the footprint it was created with."""

  @staticmethod
  def _make_tip(name: str) -> Tip:
    return Tip(
      name=name,
      has_filter=False,
      total_tip_length=50.0,
      maximal_volume=300.0,
      fitting_depth=8.0,
    )

  def test_size_x_and_size_y_are_not_transposed(self):
    spot = TipSpot(name="spot", size_x=8.0, size_y=5.0, size_z=2.0, make_tip=self._make_tip)

    self.assertEqual(spot.get_size_x(), 8.0)
    self.assertEqual(spot.get_size_y(), 5.0)
    self.assertEqual(spot.center(), Coordinate(4.0, 2.5, 0.0))

  def test_serialization_round_trip_preserves_footprint(self):
    spot = TipSpot(name="spot", size_x=8.0, size_y=5.0, size_z=2.0, make_tip=self._make_tip)

    round_tripped = TipSpot.deserialize(spot.serialize())

    self.assertEqual(round_tripped.get_size_x(), spot.get_size_x())
    self.assertEqual(round_tripped.get_size_y(), spot.get_size_y())
