import dataclasses
import json
import pathlib
import tempfile
import unittest
from typing import Callable, Optional, Tuple, cast

from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import (
  EXTENSION_HOUSING_SIZE_X,
  RECORDING_STAR,
  RECORDING_STAR_HEAD384,
  STAR,
  STAR_DECK_LOCATION,
  STAR_SIZE_X,
  STARDevice,
  STARLet,
  STARPlus,
)
from pylabrobot.hamilton.star.driver.configuration import (
  DeviceConfiguration,
  read_configuration,
)
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.hamilton.hamilton_decks import STAR_NUM_TRACKS, STARLET_NUM_TRACKS
from pylabrobot.resources.resource import Resource
from pylabrobot.serializer import serialize

# The device this package ships a recording of, read through the one reader there is: tests need a
# device to start from, and this is the one they stand in for.
RECORDED_DEVICE = cast(DeviceConfiguration, read_configuration(RECORDING_STAR)["device"])


def declaring(**parts: object) -> str:
  """The shipped recording with parts swapped out, written where it can be read back.

  A declaration is read from a file and nothing else, so a test that needs a device no recording
  describes writes one. Everything not named here stays as the recorded STAR has it.

  Args:
    parts: `device` for the device itself, or a feature name for something the left arm carries.

  Returns:
    The path it was written to.
  """
  tree = json.loads(pathlib.Path(RECORDING_STAR).read_text())
  for name, part in parts.items():
    assert dataclasses.is_dataclass(part) and not isinstance(part, type)
    if name == "device":
      tree["device"] = serialize(dataclasses.asdict(part))
    else:
      tree["arms"]["left"][name] = serialize(dataclasses.asdict(part))
  written = pathlib.Path(tempfile.mkdtemp()) / "declared.json"
  written.write_text(json.dumps(tree))
  return str(written)


class TestConstruction(unittest.IsolatedAsyncioTestCase):
  """What the device wires up when it is built."""

  def test_the_device_deck_is_what_gets_modelled(self):
    """The deck the device carries is its child and is what the driver models into, whether
    the driver was built here or handed in pointing at another deck."""
    star = STAR(simulation=True)
    self.assertIs(star.driver.deck, star.deck)
    self.assertIn(star.deck, star.children)

    deck = STARDeck()
    supplied = STARDevice(
      deck=deck,
      driver=STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR),
    )
    self.assertIs(supplied.driver.deck, deck)

  def test_needs_something_to_drive(self):
    with self.assertRaises(ValueError):
      STARDevice(deck=STARDeck())


class TestFactories(unittest.IsolatedAsyncioTestCase):
  """Each factory builds one device, on the deck that device has."""

  def test_each_factory_builds_its_own_deck(self):
    self.assertEqual(STAR(simulation=True).deck.num_tracks, STAR_NUM_TRACKS)
    self.assertEqual(STARLet(simulation=True).deck.num_tracks, STARLET_NUM_TRACKS)

  def test_extension_housing_stands_to_the_left(self):
    """The housing is a resource beside the chassis, not something that grows the device.

    It bolts to the left, so it sits at a negative x. Growing the device instead would move its
    origin, and everything measured from that origin with it.
    """
    star = STAR(simulation=True)
    self.assertEqual(star.get_absolute_size_x(), STAR_SIZE_X)
    self.assertEqual(cast(Coordinate, star.deck.location).x, STAR_DECK_LOCATION.x)

    housing = star.get_resource(f"{star.name}_left_extension_housing")
    self.assertEqual(cast(Coordinate, housing.location).x, -EXTENSION_HOUSING_SIZE_X)
    self.assertEqual(housing.get_absolute_size_x(), EXTENSION_HOUSING_SIZE_X)

  def test_extension_housing_is_fitted_unless_declined(self):
    def fitted(star):
      return any(child.name == f"{star.name}_left_extension_housing" for child in star.children)

    self.assertTrue(fitted(STAR(simulation=True)))
    self.assertFalse(fitted(STAR(simulation=True, extension_housing=False)))


class TestCapabilities(unittest.IsolatedAsyncioTestCase):
  """The device reads its features through the driver, which builds only what discovery
  found. A feature the device does not report is None rather than an object that cannot work."""

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
    # An arm that carries nothing: what the device reports at device level and what each
    # arm reports about itself agree on a real one, so the fixture makes them agree here.
    bare = dataclasses.replace(
      RECORDED_DEVICE,
      num_pip_channels=0,
      head96_installed=False,
      autoload_installed=False,
      left_arm=BARE_X_ARM,
      right_arm=None,
    )
    star = STARDevice(
      deck=STARDeck(),
      driver=STARSimulationDriver(
        deck=STARDeck(),
        declared_configuration_json=declaring(device=bare),
      ),
    )
    await star.setup()
    for name in ("pipettes", "head96", "head384", "autoload", "right_x_arm", "front_cover"):
      self.assertIsNone(getattr(star, name), name)


class TestComponentNames(unittest.IsolatedAsyncioTestCase):
  """Named devices can share a tree before and after discovery."""

  async def test_device_components_are_unique_and_reused(self):
    """Every built-in component, including nested head and iSWAP parts, belongs to its name."""
    bench = Resource(name="bench", size_x=10000, size_y=2000, size_z=2000)
    cases: Tuple[Tuple[Callable[..., STARDevice], str, Optional[str], bool], ...] = (
      (STAR, "star_a", None, False),
      (STAR, "star_b", RECORDING_STAR_HEAD384, False),
      (STARLet, "starlet", None, True),
      (STARPlus, "starplus", None, False),
    )
    for index, (factory, name, recording, side_panel) in enumerate(cases):
      with self.subTest(name=name):
        star = factory(
          name=name,
          simulation=True,
          declared_configuration_json=recording,
          extension_housing=not side_panel,
          left_side_panel_installed=side_panel,
        )
        self.assertEqual(star.deck.name, f"{name}_deck")
        self.assertTrue(all(child.name.startswith(f"{name}_") for child in star.get_all_children()))
        bench.assign_child_resource(star, location=Coordinate(index * 2500, 0, 0))
        try:
          await star.setup()
          components = star.get_all_children()
          self.assertTrue(all(child.name.startswith(f"{name}_") for child in components))
          for child in components:
            self.assertIs(bench.get_resource(child.name), child)
          assert star.pipettes is not None
          self.assertEqual(star.pipettes.resources[0].name, f"{name}_pipette_channel_0")
          if star.head96 is not None:
            self.assertEqual(
              star.head96.configuration.tip_discard_location,
              star.head96._position_centred_in(star.deck.get_trash_area96()),
            )
          await star.stop()
          await star.setup()
          self.assertEqual(
            [id(child) for child in star.get_all_children()], [id(child) for child in components]
          )
        finally:
          await star.stop()

  async def test_supplied_deck_and_labware_keep_their_names(self):
    """Discovery uses the device name without renaming caller-owned deck resources."""
    deck = STARDeck(name="custom_deck")
    labware = Resource(name="user_labware", size_x=100, size_y=100, size_z=10)
    deck.assign_child_resource(labware, track=1)
    existing_names = [child.name for child in deck.get_all_children()]
    star = STAR(name="star_a", deck=deck, simulation=True)
    try:
      await star.setup()
      self.assertIs(star.deck, deck)
      self.assertIs(star.get_resource("user_labware"), labware)
      for name in existing_names:
        self.assertTrue(star.has_resource(name))
      assert star.x_arm.resource is not None
      self.assertEqual(star.x_arm.resource.name, "star_a_left_x_arm")
      self.assertEqual(star.deck.get_trash_area96().name, "custom_deck_trash_core96")
    finally:
      await star.stop()
