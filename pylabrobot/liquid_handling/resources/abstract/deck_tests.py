import unittest

from pylabrobot.liquid_handling.resources.abstract.deck import Deck, Resource


class DeckTests(unittest.TestCase):
  """ Tests for the `Deck` class. """

  def test_assign_resource(self):
    deck = Deck()
    resource = Resource(name="resource", size_x=1, size_y=1, size_z=1)
    deck.assign_child_resource(resource)
    self.assertEqual(deck.get_resource("resource"), resource)

  def test_assign_resource_twice(self):
    deck = Deck()
    resource = Resource(name="resource", size_x=1, size_y=1, size_z=1)
    deck.assign_child_resource(resource)
    with self.assertRaises(ValueError):
      deck.assign_child_resource(resource)

  def test_clear(self):
    deck = Deck()
    resource = Resource(name="resource", size_x=1, size_y=1, size_z=1)
    deck.assign_child_resource(resource)
    deck.clear()
    self.assertEqual(deck.get_resource("resource"), None)
