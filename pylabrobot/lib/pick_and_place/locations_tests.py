import unittest

from pylabrobot.lib.pick_and_place.locations import (
  get_grip_height,
  get_grip_location,
  get_pick_location,
  get_place_location,
  get_rotation_wrt,
  get_rotation_wrt_destination,
)
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
from pylabrobot.utils.linalg import matrix_multiply_3x3


def assert_coordinates_equal(test: unittest.TestCase, got: Coordinate, expected: Coordinate):
  for a, b in zip((got.x, got.y, got.z), (expected.x, expected.y, expected.z)):
    test.assertAlmostEqual(a, b, places=6)


class GetPickLocationTests(unittest.TestCase):
  def test_its_origin_in_the_deck_frame(self):
    deck = Deck(size_x=1000, size_y=1000, size_z=10)
    holder = ResourceHolder("holder", size_x=130, size_y=90, size_z=5)
    deck.assign_child_resource(holder, location=Coordinate(100, 200, 10))
    plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    holder.assign_child_resource(plate)
    self.assertEqual(get_pick_location(plate, deck), Coordinate(100, 200, 10))


class GetPlaceLocationTests(unittest.TestCase):
  def setUp(self):
    self.deck = Deck(size_x=1000, size_y=1000, size_z=10)
    self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    self.deck.assign_child_resource(self.plate, location=Coordinate(10, 10, 0))

  def test_a_coordinate_is_the_location_itself(self):
    to = Coordinate(300, 200, 50)
    self.assertEqual(get_place_location(self.plate, to, self.deck), to)

  def test_a_resource_holder_places_it_as_it_does_itself(self):
    holder = ResourceHolder(
      "holder", size_x=130, size_y=90, size_z=5, child_location=Coordinate(1, 2, 3)
    )
    self.deck.assign_child_resource(holder, location=Coordinate(100, 200, 10))
    self.assertEqual(get_place_location(self.plate, holder, self.deck), Coordinate(101, 202, 13))

  def test_a_rotated_resource_holder_keeps_the_resource_turned_as_it_is(self):
    holder = ResourceHolder("holder", size_x=90, size_y=130, size_z=5, rotation=Rotation(z=90))
    self.deck.assign_child_resource(holder, location=Coordinate(400, 200, 10))
    # Turned -90 against the holder: its origin sits size_x along the holder's y.
    location = get_place_location(self.plate, holder, self.deck)
    expected = Coordinate(400 - self.plate.get_size_x(), 200, 10)
    assert_coordinates_equal(self, location, expected)

  def test_a_plate_adapter_aligns_the_plate_wells_with_its_holes(self):
    magnet = alpaqua_96_plateadapter_magnum_flx(name="magnet")
    magnet.plate_z_offset = 0.62
    self.deck.assign_child_resource(magnet, location=Coordinate(100, 200, 10))
    location = get_place_location(self.plate, magnet, self.deck)
    assert_coordinates_equal(self, location, Coordinate(100.05, 199.56, 38.12))

  def test_a_plate_adapter_refuses_anything_but_a_plate(self):
    magnet = alpaqua_96_plateadapter_magnum_flx(name="magnet")
    self.deck.assign_child_resource(magnet, location=Coordinate(100, 200, 10))
    box = Resource("box", size_x=10, size_y=10, size_z=10)
    self.deck.assign_child_resource(box, location=Coordinate(0, 0, 0))
    with self.assertRaises(ValueError):
      get_place_location(box, magnet, self.deck)

  def test_a_resource_stack_puts_it_on_top(self):
    stack = ResourceStack("stack", "z")
    self.deck.assign_child_resource(stack, location=Coordinate(500, 100, 0))
    stack.assign_child_resource(Resource("bottom", size_x=127, size_y=85, size_z=20))
    self.assertEqual(get_place_location(self.plate, stack, self.deck), Coordinate(500, 100, 20))

  def test_a_resource_stack_takes_it_turned_by_180_degrees(self):
    stack = ResourceStack("stack", "z")
    self.deck.assign_child_resource(stack, location=Coordinate(500, 100, 0))
    self.plate.rotate(z=180)
    location = get_place_location(self.plate, stack, self.deck)
    expected = Coordinate(500 + self.plate.get_size_x(), 100 + self.plate.get_size_y(), 0)
    assert_coordinates_equal(self, location, expected)

  def test_a_resource_stack_refuses_it_turned_by_90_degrees(self):
    stack = ResourceStack("stack", "z")
    self.deck.assign_child_resource(stack, location=Coordinate(500, 100, 0))
    with self.assertRaises(ValueError):
      get_place_location(self.plate, stack, self.deck, rotation_applied_by_move=90)

  def test_a_resource_stack_growing_sideways_refuses(self):
    stack = ResourceStack("stack", "x")
    self.deck.assign_child_resource(stack, location=Coordinate(500, 100, 0))
    with self.assertRaises(ValueError):
      get_place_location(self.plate, stack, self.deck)

  def test_a_lid_goes_onto_its_resource_as_it_seats_one(self):
    size_x, size_y = self.plate.get_size_x(), self.plate.get_size_y()
    lid = Lid("lid", size_x=size_x, size_y=size_y, size_z=8, nesting_z_height=3)
    self.deck.assign_child_resource(lid, location=Coordinate(300, 300, 0))
    expected = self.plate.get_location_wrt(self.deck) + self.plate.get_lid_location(lid)
    self.assertEqual(get_place_location(lid, self.plate, self.deck), expected)

  def test_a_trash_takes_it_at_its_origin(self):
    trash = Trash("trash", size_x=100, size_y=100, size_z=50)
    self.deck.assign_child_resource(trash, location=Coordinate(700, 100, 0))
    self.assertEqual(get_place_location(self.plate, trash, self.deck), Coordinate(700, 100, 0))

  def test_any_other_resource_takes_it_at_its_origin(self):
    box = Resource("box", size_x=200, size_y=200, size_z=10)
    self.deck.assign_child_resource(box, location=Coordinate(600, 600, 0))
    self.assertEqual(get_place_location(self.plate, box, self.deck), Coordinate(600, 600, 0))


class GetRotationWrtTests(unittest.TestCase):
  """Its rotation is the one that, after the frame's, gives its absolute rotation."""

  ANGLES = (-450, -270, -90, -37.5, 0, 12.3, 90, 180, 270, 359.9, 450)

  def assert_matrices_equal(self, got: Rotation, expected: Rotation):
    for got_row, expected_row in zip(got.get_rotation_matrix(), expected.get_rotation_matrix()):
      for a, b in zip(got_row, expected_row):
        self.assertAlmostEqual(a, b, delta=1e-9)

  def test_about_z_only_it_is_the_difference_of_the_angles(self):
    deck = Resource("deck", size_x=1000, size_y=1000, size_z=10)
    for frame_z in self.ANGLES:
      for own_z in self.ANGLES:
        with self.subTest(frame_z=frame_z, own_z=own_z):
          deck.rotation = Rotation(z=frame_z)
          carrier = Resource("carrier", size_x=100, size_y=100, size_z=10)
          deck.assign_child_resource(carrier, location=Coordinate(10, 20, 0))
          carrier.rotation = Rotation(z=own_z)
          turned, frame = carrier.get_absolute_rotation(), deck.get_absolute_rotation()
          rotation = get_rotation_wrt(carrier, deck)
          self.assertEqual(
            (rotation.x, rotation.y, rotation.z),
            (turned.x - frame.x, turned.y - frame.y, turned.z - frame.z),
          )
          deck.unassign_child_resource(carrier)

  def test_a_child_of_a_frame_turned_about_other_axes(self):
    for frame_rotation, own in (
      (Rotation(x=90), Rotation(z=90)),
      (Rotation(y=90), Rotation(z=90)),
      (Rotation(x=45, y=30), Rotation(z=-60)),
      (Rotation(x=30, y=-20, z=110), Rotation(x=-15, y=70, z=200)),
    ):
      with self.subTest(frame=frame_rotation, own=own):
        frame = Resource("frame", size_x=100, size_y=100, size_z=10, rotation=frame_rotation)
        child = Resource("child", size_x=10, size_y=10, size_z=10, rotation=own)
        frame.assign_child_resource(child, location=Coordinate.zero())
        rotation = get_rotation_wrt(child, frame)
        self.assert_matrices_equal(
          frame.get_absolute_rotation() + rotation, child.get_absolute_rotation()
        )
        assert_coordinates_equal(
          self, Coordinate(10, 20, 30).rotated(rotation), Coordinate(10, 20, 30).rotated(own)
        )

  def test_a_point_along_the_child_lies_along_the_frame_where_it_truly_is(self):
    # A frame turned about x holds a child turned about z: the child's x axis is the frame's y.
    frame = Resource("frame", size_x=100, size_y=100, size_z=10, rotation=Rotation(x=90))
    child = Resource("child", size_x=10, size_y=10, size_z=10, rotation=Rotation(z=90))
    frame.assign_child_resource(child, location=Coordinate.zero())
    assert_coordinates_equal(
      self, Coordinate(10, 0, 0).rotated(get_rotation_wrt(child, frame)), Coordinate(0, 10, 0)
    )

  def test_a_plate_on_a_carrier_turned_about_x_on_a_deck(self):
    # The deck sits tilted on the device carrying it, so its own axes are turned about y too.
    deck = Deck(size_x=1000, size_y=1000, size_z=10)
    deck.rotation = Rotation(y=20, z=90)
    carrier = Resource("carrier", size_x=200, size_y=100, size_z=10, rotation=Rotation(x=30))
    deck.assign_child_resource(carrier, location=Coordinate(100, 200, 0))
    plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    plate.rotation = Rotation(z=90)
    carrier.assign_child_resource(plate, location=Coordinate(5, 5, 10))
    expected = matrix_multiply_3x3(
      Rotation(x=30).get_rotation_matrix(), Rotation(z=90).get_rotation_matrix()
    )
    got = get_rotation_wrt(plate, deck).get_rotation_matrix()
    for got_row, expected_row in zip(got, expected):
      for a, b in zip(got_row, expected_row):
        self.assertAlmostEqual(a, b, delta=1e-9)
    self.assert_matrices_equal(get_rotation_wrt(plate, carrier), Rotation(z=90))


class GetRotationWrtDestinationTests(unittest.TestCase):
  def test_its_rotation_and_the_move_less_the_destination(self):
    holder = ResourceHolder("holder", size_x=90, size_y=130, size_z=5, rotation=Rotation(z=90))
    plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    plate.rotate(z=180)
    deck = Resource("deck", size_x=1000, size_y=1000, size_z=10)
    self.assertEqual(get_rotation_wrt_destination(plate, holder, deck, 90), 180)
    self.assertEqual(get_rotation_wrt_destination(plate, Coordinate.zero(), deck), 180)


class TurnedDeviceTests(unittest.TestCase):
  """A deck carried by a turned device: offsets follow the deck's axes, not the room's."""

  def setUp(self):
    room = Resource("room", size_x=9000, size_y=9000, size_z=3000)
    room.location = Coordinate.zero()
    self.deck = Resource("deck", size_x=1000, size_y=600, size_z=10)
    room.assign_child_resource(self.deck, location=Coordinate(3000, 3000, 0))
    self.deck.rotation = Rotation(z=90)
    self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")

  def test_a_plate_adapter_on_a_turned_deck_places_it_along_the_deck(self):
    magnet = alpaqua_96_plateadapter_magnum_flx(name="magnet")
    self.deck.assign_child_resource(magnet, location=Coordinate(400, 70, 178))
    self.deck.assign_child_resource(self.plate, location=Coordinate(700, 300, 0))
    self.assertEqual(
      get_place_location(self.plate, magnet, self.deck),
      Coordinate(400, 70, 178) + magnet.compute_plate_location(self.plate),
    )

  def test_a_holder_turned_on_a_turned_deck_turns_its_offset_by_its_own_turn_only(self):
    holder = ResourceHolder(
      "holder",
      size_x=90,
      size_y=130,
      size_z=5,
      rotation=Rotation(z=90),
      child_location=Coordinate(4, 4, 2),
    )
    self.deck.assign_child_resource(holder, location=Coordinate(400, 70, 0))
    # Upright on the deck, so a quarter turn against the holder, whose offset turns it back.
    self.deck.assign_child_resource(self.plate, location=Coordinate(700, 300, 0))
    seated = holder.get_default_child_location(self.plate.rotated(z=-90))
    self.assertEqual(
      get_place_location(self.plate, holder, self.deck),
      Coordinate(400, 70, 0) + seated.rotated(Rotation(z=90)),
    )


class GetGripHeightTests(unittest.TestCase):
  def setUp(self):
    self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")

  def test_from_the_top_it_is_that_far_below_the_top(self):
    self.assertAlmostEqual(
      get_grip_height(self.plate, pickup_distance_from_top=6), self.plate.get_size_z() - 6
    )

  def test_from_the_bottom_it_is_that_distance(self):
    self.assertEqual(get_grip_height(self.plate, pickup_distance_from_bottom=4), 4)

  def test_both_given_is_refused(self):
    with self.assertRaisesRegex(ValueError, "exactly one"):
      get_grip_height(self.plate, pickup_distance_from_top=6, pickup_distance_from_bottom=4)

  def test_neither_given_is_refused(self):
    with self.assertRaisesRegex(ValueError, "exactly one"):
      get_grip_height(self.plate)

  def test_a_grip_outside_the_resource_is_refused(self):
    size_z = self.plate.get_size_z()
    for kwargs in (
      {"pickup_distance_from_top": size_z + 1},
      {"pickup_distance_from_top": -1},
      {"pickup_distance_from_bottom": size_z + 1},
      {"pickup_distance_from_bottom": -1},
    ):
      with self.subTest(**kwargs), self.assertRaisesRegex(ValueError, "within"):
        get_grip_height(self.plate, **kwargs)


class GetGripLocationTests(unittest.TestCase):
  def test_the_centre_at_the_grip_height(self):
    deck = Resource("deck", size_x=1000, size_y=600, size_z=10)
    plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    deck.assign_child_resource(plate, location=Coordinate(100, 50, 20))
    grip = get_grip_location(plate, Coordinate(100, 50, 20), deck, 8)
    self.assertEqual(
      grip, Coordinate(100 + plate.get_size_x() / 2, 50 + plate.get_size_y() / 2, 20 + 8)
    )


if __name__ == "__main__":
  unittest.main()
