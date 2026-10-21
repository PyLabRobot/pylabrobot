import unittest

from pylabrobot.lib.pick_and_place.assignment import assign_placed_resource
from pylabrobot.lib.pick_and_place.locations import get_place_location
from pylabrobot.resources.alpaqua.magnetic_racks import alpaqua_96_plateadapter_magnum_flx
from pylabrobot.resources.azenta.plates import azenta_96_wellplate_200uL_Vb_4titudeframestar
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash


class AssignPlacedResourceTests(unittest.TestCase):
  """Each destination seats it as it does itself, where `get_place_location` aims it."""

  def setUp(self):
    self.deck = Deck(size_x=1000, size_y=1000, size_z=10)
    self.source = ResourceHolder("source", size_x=130, size_y=90, size_z=5)
    self.deck.assign_child_resource(self.source, location=Coordinate(10, 10, 0))
    self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    self.source.assign_child_resource(self.plate)

  def place(self, resource: Resource, destination, **kwargs) -> Coordinate:
    """Place `resource` where `get_place_location` aims it, and return that location."""
    location = get_place_location(resource, destination, self.deck, **kwargs)
    assign_placed_resource(resource, destination, self.deck, location=location, **kwargs)
    return location

  def assert_where_aimed(self, resource: Resource, location: Coordinate):
    got = resource.get_location_wrt(self.deck)
    for a, b in zip((got.x, got.y, got.z), (location.x, location.y, location.z)):
      self.assertAlmostEqual(a, b, places=6)

  def test_a_coordinate_puts_it_on_the_deck(self):
    location = self.place(self.plate, Coordinate(300, 200, 50))
    self.assertIs(self.plate.parent, self.deck)
    self.assertEqual(self.plate.location, Coordinate(300, 200, 50))
    self.assertIsNone(self.source.resource)
    self.assert_where_aimed(self.plate, location)

  def test_a_resource_holder_seats_it(self):
    holder = ResourceHolder("holder", size_x=130, size_y=90, size_z=5)
    self.deck.assign_child_resource(holder, location=Coordinate(100, 200, 10))
    location = self.place(self.plate, holder)
    self.assertIs(holder.resource, self.plate)
    self.assertEqual(self.plate.location, Coordinate.zero())
    self.assert_where_aimed(self.plate, location)

  def test_a_rotated_resource_holder_keeps_it_turned_as_it_was(self):
    holder = ResourceHolder("holder", size_x=90, size_y=130, size_z=5, rotation=Rotation(z=90))
    self.deck.assign_child_resource(holder, location=Coordinate(400, 200, 10))
    location = self.place(self.plate, holder)
    self.assertIs(holder.resource, self.plate)
    self.assertEqual(self.plate.get_absolute_rotation().z % 360, 0)
    self.assert_where_aimed(self.plate, location)

  def test_a_turn_by_the_move_is_kept(self):
    holder = ResourceHolder("holder", size_x=130, size_y=90, size_z=5)
    self.deck.assign_child_resource(holder, location=Coordinate(100, 200, 10))
    location = self.place(self.plate, holder, rotation_applied_by_move=180)
    self.assertEqual(self.plate.get_absolute_rotation().z % 360, 180)
    self.assert_where_aimed(self.plate, location)

  def test_a_plate_adapter_seats_the_plate_on_its_holes(self):
    magnet = alpaqua_96_plateadapter_magnum_flx(name="magnet")
    magnet.plate_z_offset = 0.62
    self.deck.assign_child_resource(magnet, location=Coordinate(100, 200, 10))
    location = self.place(self.plate, magnet)
    self.assertIs(self.plate.parent, magnet)
    self.assertEqual(self.plate.location, magnet.compute_plate_location(self.plate))
    self.assert_where_aimed(self.plate, location)

  def test_a_resource_stack_puts_it_on_top(self):
    stack = ResourceStack("stack", "z")
    self.deck.assign_child_resource(stack, location=Coordinate(500, 100, 0))
    stack.assign_child_resource(Resource("bottom", size_x=127, size_y=85, size_z=20))
    location = self.place(self.plate, stack)
    self.assertIs(stack.get_top_item(), self.plate)
    self.assert_where_aimed(self.plate, location)

  def test_a_resource_stack_takes_it_turned_by_180_degrees(self):
    stack = ResourceStack("stack", "z")
    self.deck.assign_child_resource(stack, location=Coordinate(500, 100, 0))
    location = self.place(self.plate, stack, rotation_applied_by_move=180)
    self.assertIs(stack.get_top_item(), self.plate)
    self.assert_where_aimed(self.plate, location)

  def test_a_lid_goes_onto_its_resource(self):
    size_x, size_y = self.plate.get_size_x(), self.plate.get_size_y()
    lid = Lid("lid", size_x=size_x, size_y=size_y, size_z=8, nesting_z_height=3)
    self.deck.assign_child_resource(lid, location=Coordinate(300, 300, 0))
    location = self.place(lid, self.plate)
    self.assertIs(self.plate.lid, lid)
    self.assert_where_aimed(lid, location)

  def test_a_trash_discards_it(self):
    trash = Trash("trash", size_x=100, size_y=100, size_z=50)
    self.deck.assign_child_resource(trash, location=Coordinate(700, 100, 0))
    self.place(self.plate, trash)
    self.assertIsNone(self.plate.parent)
    self.assertIsNone(self.source.resource)

  def test_any_other_resource_takes_it_at_its_origin(self):
    box = Resource("box", size_x=200, size_y=200, size_z=10)
    self.deck.assign_child_resource(box, location=Coordinate(600, 600, 0))
    location = self.place(self.plate, box)
    self.assertIs(self.plate.parent, box)
    self.assertEqual(self.plate.location, Coordinate.zero())
    self.assert_where_aimed(self.plate, location)


if __name__ == "__main__":
  unittest.main()
