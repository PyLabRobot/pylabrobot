import dataclasses
import json
import pathlib
import tempfile
import unittest
from typing import cast
from unittest.mock import patch

from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import (
  EXTENSION_HOUSING_SIZE_X,
  RECORDING_STAR,
  STAR,
  STAR_DECK_LOCATION,
  STAR_SIZE_X,
  STARDevice,
  STARLet,
)
from pylabrobot.hamilton.star.driver.configuration import (
  DeviceConfiguration,
  read_configuration,
)
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.hamilton.hamilton_decks import STAR_NUM_TRACKS, STARLET_NUM_TRACKS
from pylabrobot.resources.hamilton.tip_creators import hamilton_tip_300uL
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
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

    housing = star.get_resource("left_extension_housing")
    self.assertEqual(cast(Coordinate, housing.location).x, -EXTENSION_HOUSING_SIZE_X)
    self.assertEqual(housing.get_absolute_size_x(), EXTENSION_HOUSING_SIZE_X)

  def test_extension_housing_is_fitted_unless_declined(self):
    def fitted(star):
      return any(child.name == "left_extension_housing" for child in star.children)

    self.assertTrue(fitted(STAR(simulation=True)))
    self.assertFalse(fitted(STAR(simulation=True, extension_housing=False)))


class TestCapabilities(unittest.IsolatedAsyncioTestCase):
  """The device reads its features through the driver, which builds only what discovery
  found. A feature the device does not report is None rather than an object that cannot work."""

  async def test_channels_belong_to_device_and_follow_arm_and_channel_moves(self):
    star = STAR(simulation=True)
    bench = Resource("bench", size_x=3000, size_y=2000, size_z=1000)
    bench.assign_child_resource(star, location=Coordinate(200, 300, 1000))
    with patch("pylabrobot.resources.hamilton.hamilton_decks.logger.warning") as warning:
      await star.setup()
    self.assertEqual(warning.call_args_list, [])
    pipettes = star.pipettes
    assert pipettes is not None
    channels = list(pipettes.resources)
    for channel in channels:
      self.assertIs(channel.parent, star)
      self.assertFalse(channel.is_in_subtree_of(star.deck))

    shaft = next(child for child in channels[0].children if isinstance(child, TipMountingShaft))
    tip = hamilton_tip_300uL(name="carried_tip")
    with patch("pylabrobot.resources.hamilton.hamilton_decks.logger.warning") as warning:
      shaft.mount_tip(tip)
    self.assertEqual(warning.call_args_list, [])

    await star.x_arm.move_x(500)
    await pipettes.move_to_y_positions({0: 450}, make_space=False)
    await pipettes.move_stop_disc_to_z_position(0, 250)
    deck_origin = star.deck.get_absolute_location()
    position = shaft.get_absolute_location(x="c", y="c") - deck_origin
    self.assertAlmostEqual(position.x, 500)
    self.assertAlmostEqual(position.y, 450, places=1)
    self.assertAlmostEqual(position.z, 250, places=1)
    self.assertAlmostEqual(await pipettes.request_stop_disc_z_position(0), 250, places=1)
    for index in range(len(channels)):
      reference = pipettes.get_reference_point_location(index)
      assert reference is not None
      self.assertAlmostEqual(reference.x, 500)

    # Reads after a partially completed move must propagate the measured X to the channels.
    star.x_arm.update_location_by_reference_point(640)
    self.assertAlmostEqual((shaft.get_absolute_location(x="c") - deck_origin).x, 640)
    self.assertAlmostEqual((shaft.get_absolute_location() - deck_origin).z, 250, places=1)

    await star.setup()
    for index, channel in enumerate(channels):
      self.assertIs(pipettes.resources[index], channel)
    self.assertIs(shaft.tip, tip)

  async def test_channels_from_standalone_driver_move_to_device_on_setup(self):
    deck = STARDeck()
    driver = STARSimulationDriver(deck=deck, declared_configuration_json=RECORDING_STAR)
    await driver.setup()
    assert driver.pipettes is not None
    channel = driver.pipettes.resources[0]
    before = driver.pipettes.get_reference_point_location(0)
    star = STARDevice(deck=deck, driver=driver, deck_location=Coordinate(110, 100, 80))
    await star.setup()
    self.assertIs(driver.pipettes.resources[0], channel)
    self.assertIs(channel.parent, star)
    self.assertEqual(driver.pipettes.get_reference_point_location(0), before)

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
