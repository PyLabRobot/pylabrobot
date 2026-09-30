import unittest
from typing import Optional

from pylabrobot.lib.spatial.occupancy import get_resource_at_location
from pylabrobot.resources.azenta.plates import azenta_96_wellplate_200uL_Vb_4titudeframestar
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import PLT_CAR_L5AC_A00, STARDeck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation


class GetResourceAtLocationTests(unittest.TestCase):
  def setUp(self):
    self.deck = STARDeck()
    self.carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
    self.deck.assign_child_resource(self.carrier, track=30)
    self.carrier[0] = self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")

  def on_deck(self, resource: Resource, x: str, y: str, z: str, dz: float = 0.0) -> Coordinate:
    location: Coordinate = resource.get_location_wrt(self.deck, x, y, z)
    return location + Coordinate(0, 0, dz)

  def find(self, location: Coordinate, **kwargs) -> Optional[Resource]:
    return get_resource_at_location(location, reference_frame=self.deck, **kwargs)

  def test_a_point_inside_a_plate_is_the_plate_not_its_carrier(self):
    self.assertIs(self.find(self.on_deck(self.plate, "c", "c", "c")), self.plate)

  def test_a_point_inside_an_empty_site_is_the_site_or_its_carrier(self):
    found = self.find(self.on_deck(self.carrier[2], "c", "c", "b", 0.5))
    assert found is not None
    self.assertTrue(found.is_in_subtree_of(self.carrier))

  def test_a_point_on_a_top_face_is_in_nothing(self):
    self.assertIsNone(self.find(self.on_deck(self.carrier, "c", "c", "t")))

  def test_a_point_on_an_empty_stretch_of_deck_is_in_nothing(self):
    self.assertIsNone(self.find(Coordinate(400.0, 200.0, 100.0)))

  def test_an_excluded_resource_and_what_it_carries_are_left_out(self):
    point = self.on_deck(self.plate, "c", "c", "c")
    self.assertIsNone(self.find(point, exclude=[self.carrier]))
    found = self.find(point, exclude=[self.plate])
    assert found is not None
    self.assertTrue(found.is_in_subtree_of(self.carrier))
    self.assertFalse(found.is_in_subtree_of(self.plate))

  def test_the_point_is_read_in_the_reference_frame(self):
    point = self.plate.get_location_wrt(self.carrier, "c", "c", "c")
    found = get_resource_at_location(point, reference_frame=self.carrier)
    self.assertIs(found, self.plate)


class GetResourceAtLocationBoxTests(unittest.TestCase):
  def setUp(self):
    self.frame = Resource(name="frame", size_x=200, size_y=200, size_z=200)

  def test_a_box_holds_its_left_front_bottom_faces_but_not_its_right_back_top_ones(self):
    box = Resource(name="box", size_x=10, size_y=10, size_z=10)
    self.frame.assign_child_resource(box, location=Coordinate(50, 50, 0))
    self.assertIs(get_resource_at_location(Coordinate(50, 50, 0), self.frame), box)
    for point in [Coordinate(60, 55, 5), Coordinate(55, 60, 5), Coordinate(55, 55, 10)]:
      self.assertIsNone(get_resource_at_location(point, self.frame))

  def test_a_resource_rotated_about_z_is_its_rotated_footprint(self):
    bar = Resource(name="bar", size_x=100, size_y=10, size_z=10, rotation=Rotation(z=90))
    self.frame.assign_child_resource(bar, location=Coordinate(50, 20, 0))
    self.assertIs(get_resource_at_location(Coordinate(45, 100, 5), self.frame), bar)
    self.assertIsNone(get_resource_at_location(Coordinate(55, 25, 5), self.frame))
