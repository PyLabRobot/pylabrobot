import unittest

from pylabrobot.resources import Coordinate, Deck
from pylabrobot.resources.hamilton import VantageDeck


class VantageDeckTests(unittest.TestCase):
  def test_a_1_3_m_deck_has_54_tracks(self):
    deck = VantageDeck(size=1.3)
    self.assertEqual(deck.num_tracks, 54)
    self.assertEqual(deck.get_absolute_size_x(), 1237.5)
    self.assertEqual(deck.get_absolute_size_y(), 653.5)

  def test_a_2_0_m_deck_has_84_tracks_on_the_same_raster(self):
    deck = VantageDeck(size=2.0)
    self.assertEqual(deck.num_tracks, 84)
    self.assertEqual(deck.get_absolute_size_x(), 1912.5)
    self.assertEqual(deck.get_absolute_size_y(), 653.5)
    self.assertEqual(deck.track_to_location(1), Coordinate(x=32.5, y=63, z=100))
    self.assertEqual(deck.track_to_location(84), Coordinate(x=32.5 + 83 * 22.5, y=63, z=100))

  def test_a_2_0_m_deck_puts_the_trash_at_the_channel_waste(self):
    deck = VantageDeck(size=2.0)
    trash = deck.get_trash_area()
    self.assertEqual(trash.get_location_wrt(deck), Coordinate(x=1384.5, y=185.6, z=123.5))

  def test_an_unknown_size_is_refused(self):
    with self.assertRaises(ValueError):
      VantageDeck(size=1.8)

  def test_serialization_keeps_the_size(self):
    for size in (1.3, 2.0):
      with self.subTest(size=size):
        deck = VantageDeck(size=size)
        serialized = deck.serialize()
        self.assertEqual(serialized["size"], size)
        deck2 = Deck.deserialize(serialized)
        assert isinstance(deck2, VantageDeck)
        self.assertEqual(deck2.size, size)
        self.assertEqual(deck2.num_tracks, deck.num_tracks)
        self.assertEqual(
          deck2.get_trash_area().get_location_wrt(deck2),
          deck.get_trash_area().get_location_wrt(deck),
        )
