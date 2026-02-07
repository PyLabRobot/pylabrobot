import os
import tempfile
import unittest

from pylabrobot.resources import (
  Coordinate,
  Deck,
  Plate,
  PlateCarrier,
  PlateHolder,
  Resource,
  ResourceHolder,
  ResourceNotFoundError,
  TipCarrier,
  TipRack,
  TipSpot,
  Well,
  create_homogeneous_resources,
  create_ordered_items_2d,
  hamilton_tip_300uL_filter,
)


def _make_test_deck() -> Deck:
  return Deck(size_x=100, size_y=100, size_z=100)


class DeckTests(unittest.TestCase):
  """Tests for the `Deck` class."""

  def test_assign_resource(self):
    deck = _make_test_deck()
    resource = Resource(name="resource", size_x=1, size_y=1, size_z=1)
    deck.assign_child_resource(resource, location=Coordinate.zero())
    self.assertEqual(deck.get_resource("resource"), resource)

  def test_assign_resource_twice(self):
    deck = _make_test_deck()
    resource = Resource(name="resource", size_x=1, size_y=1, size_z=1)
    deck.assign_child_resource(resource, location=Coordinate.zero())
    with self.assertRaises(ValueError):
      deck.assign_child_resource(resource, location=Coordinate.zero())

  def test_clear(self):
    deck = _make_test_deck()
    r1 = Resource(name="r1", size_x=1, size_y=1, size_z=1)
    r2 = Resource(name="r2", size_x=1, size_y=1, size_z=1)
    r3 = Resource(name="r3", size_x=1, size_y=1, size_z=1)
    deck.assign_child_resource(r1, location=Coordinate.zero())
    deck.assign_child_resource(r2, location=Coordinate(x=2))
    deck.assign_child_resource(r3, location=Coordinate(x=4))
    deck.clear()
    with self.assertRaises(ResourceNotFoundError):
      deck.get_resource("resource")

  def test_json_serialization_standard(self):
    self.maxDiff = None
    tmp_dir = tempfile.gettempdir()

    # test with custom classes
    custom_1 = _make_test_deck()
    tc = TipCarrier(
      "tc",
      200,
      200,
      200,
      sites=create_homogeneous_resources(
        klass=ResourceHolder,
        locations=[Coordinate(10, 20, 30)],
        resource_size_x=10,
        resource_size_y=10,
        name_prefix="tc",
      ),
    )

    tc[0] = TipRack(
      "tips",
      10,
      20,
      30,
      ordered_items=create_ordered_items_2d(
        TipSpot,
        num_items_x=1,
        num_items_y=1,
        dx=-1,
        dy=-1,
        dz=-1,
        item_dx=1,
        item_dy=1,
        size_x=1,
        size_y=1,
        make_tip=hamilton_tip_300uL_filter,
      ),
    )
    pc = PlateCarrier(
      "pc",
      100,
      100,
      100,
      sites=create_homogeneous_resources(
        klass=PlateHolder,
        locations=[Coordinate(10, 20, 30)],
        resource_size_x=10,
        resource_size_y=10,
        name_prefix="pc",
        pedestal_size_z=0,
      ),
    )
    pc[0] = Plate(
      "plate",
      10,
      20,
      30,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=1,
        num_items_y=1,
        dx=-1,
        dy=-1,
        dz=-1,
        item_dx=1,
        item_dy=1,
        size_x=1,
        size_y=1,
        size_z=1,
      ),
    )
    custom_1.assign_child_resource(tc, location=Coordinate(0, 0, 0))
    custom_1.assign_child_resource(pc, location=Coordinate(100, 0, 0))

    fn = os.path.join(tmp_dir, "layout.json")
    custom_1.save(fn)
    custom_recover = Deck.load_from_json_file(fn)

    self.assertEqual(custom_1, custom_recover)
