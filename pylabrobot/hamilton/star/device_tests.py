import dataclasses
import unittest
from typing import cast

from pylabrobot.hamilton.star.device import (
  EXTENSION_HOUSING_SIZE_X,
  STAR,
  STAR_DECK_LOCATION,
  STAR_SIZE_X,
  STAR_with_extension_housing,
  STARDevice,
  STARLet,
)
from pylabrobot.hamilton.star.driver.simulator import (
  BARE_X_ARM,
  DEFAULT_STAR_CONFIGURATION,
  STARSimulationDriver,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.hamilton.hamilton_decks import STAR_NUM_RAILS, STARLET_NUM_RAILS


class TestConstruction(unittest.IsolatedAsyncioTestCase):
  """What the instrument wires up when it is built."""

  def test_the_instrument_deck_is_what_gets_modelled(self):
    """The deck the instrument carries is its child and is what the driver models into, whether
    the driver was built here or handed in pointing at another deck."""
    star = STAR(simulation=True)
    self.assertIs(star.driver.deck, star.deck)
    self.assertIn(star.deck, star.children)

    deck = STARDeck()
    supplied = STARDevice(deck=deck, driver=STARSimulationDriver(deck=STARDeck()))
    self.assertIs(supplied.driver.deck, deck)

  def test_needs_something_to_drive(self):
    with self.assertRaises(ValueError):
      STARDevice(deck=STARDeck())


class TestFactories(unittest.IsolatedAsyncioTestCase):
  """Each factory builds one machine, on the deck that machine has."""

  def test_each_factory_builds_its_own_deck(self):
    self.assertEqual(STAR(simulation=True).deck.num_rails, STAR_NUM_RAILS)
    self.assertEqual(STARLet(simulation=True).deck.num_rails, STARLET_NUM_RAILS)

  def test_extension_housing_stands_to_the_left(self):
    """The housing is a resource beside the chassis, not something that grows the instrument.

    It bolts to the left, so it sits at a negative x. Growing the instrument instead would move its
    origin, and everything measured from that origin with it.
    """
    star = STAR_with_extension_housing(simulation=True)
    self.assertEqual(star.get_absolute_size_x(), STAR_SIZE_X)
    self.assertEqual(cast(Coordinate, star.deck.location).x, STAR_DECK_LOCATION.x)

    housing = star.get_resource("left_extension_housing")
    self.assertEqual(cast(Coordinate, housing.location).x, -EXTENSION_HOUSING_SIZE_X)
    self.assertEqual(housing.get_absolute_size_x(), EXTENSION_HOUSING_SIZE_X)

  def test_extension_housing_is_absent_unless_asked_for(self):
    def fitted(star):
      return any(child.name == "left_extension_housing" for child in star.children)

    self.assertFalse(fitted(STAR(simulation=True)))
    self.assertTrue(fitted(STAR(simulation=True, extension_housing=True)))


class TestCapabilities(unittest.IsolatedAsyncioTestCase):
  """The instrument reads its capabilities through the driver, which builds only what discovery
  found. A capability the machine does not report is None rather than an object that cannot work."""

  async def test_reads_through_to_the_driver(self):
    star = STAR(simulation=True)
    await star.setup()
    for name in (
      "pipettes",
      "head96",
      "head384",
      "iswap",
      "autoload",
      "left_x_arm",
      "right_x_arm",
    ):
      self.assertIs(getattr(star, name), getattr(star.driver, name), name)

  async def test_absent_capabilities_are_none(self):
    # An arm that carries nothing: what the machine reports at instrument level and what each
    # arm reports about itself agree on a real one, so the fixture makes them agree here.
    bare = dataclasses.replace(
      DEFAULT_STAR_CONFIGURATION,
      num_pip_channels=0,
      ka_head96_installed=False,
      autoload_installed=False,
      left_arm=BARE_X_ARM,
      right_arm=None,
    )
    star = STARDevice(
      deck=STARDeck(), driver=STARSimulationDriver(configuration=bare, deck=STARDeck())
    )
    await star.setup()
    for name in ("pipettes", "head96", "head384", "autoload", "right_x_arm", "front_cover"):
      self.assertIsNone(getattr(star, name), name)
