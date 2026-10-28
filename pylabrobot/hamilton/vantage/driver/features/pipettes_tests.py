import re
import unittest
from typing import Any, List, Optional, Tuple

from pylabrobot.hamilton.vantage.driver.errors import VantageFirmwareError
from pylabrobot.hamilton.vantage.driver.features.pipettes import Pipettes, PipettesConfiguration
from pylabrobot.hamilton.vantage.driver.master import VantageDriver
from pylabrobot.hamilton.vantage.driver.master_tests import ScriptedIO, echo, machine
from pylabrobot.legacy.liquid_handling.backends.hamilton.vantage_backend import VantageBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.resources.hamilton.tip_carriers import hamilton_tip_carrier_L5
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip, TipPickupMethod
from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_300uL
from pylabrobot.resources.hamilton.vantage_decks import VantageDeck
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tip_tracking import set_tip_tracking

PIPETTES_LOGGER = "pylabrobot.hamilton.vantage.driver.features.pipettes"


def without_id(command: str) -> str:
  """A command without the id it was sent with."""
  return re.sub(r"id\d+", "", command, count=1)


class LegacyCatcher(VantageBackend):
  """The legacy backend, writing what it would send into a list instead of to a machine."""

  def __init__(self, num_channels: int):
    super().__init__()
    self.commands: List[str] = []
    self._num_channels = num_channels

  async def send_command(  # type: ignore[override]
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    cmd, _ = self._assemble_command(
      module=module, command=command, auto_id=auto_id, tip_pattern=tip_pattern, **kwargs
    )
    self.commands.append(cmd)


class PipettesTestCase(unittest.IsolatedAsyncioTestCase):
  """A driver on a machine with `num_channels` channels, discovered."""

  num_channels = 8

  async def asyncSetUp(self):
    self.io = ScriptedIO(machine(num_channels=self.num_channels))
    self.driver = VantageDriver(self.io, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()
    await self.driver.discover()
    self.io.written.clear()
    pipettes = self.driver.pipettes
    assert pipettes is not None
    self.pipettes: Pipettes = pipettes

  async def asyncTearDown(self):
    self.driver._router.stop()


class TestConfiguration(unittest.TestCase):
  """Converting a value in mm to what the wire carries."""

  def test_a_value_is_written_in_tenths_of_a_millimeter(self):
    configuration = PipettesConfiguration()
    self.assertEqual(configuration.to_increments(245.0), 2450)
    self.assertEqual(configuration.to_increments(123.5), 1235)
    self.assertEqual(configuration.to_increments(0.0), 0)
    self.assertEqual(configuration.to_increments(-7.5), -75)

  def test_a_value_finer_than_the_wire_goes_to_the_nearest_tenth(self):
    configuration = PipettesConfiguration()
    self.assertEqual(configuration.to_increments(100.26), 1003)
    self.assertEqual(configuration.to_increments(100.24), 1002)
    self.assertEqual(configuration.to_increments(-100.26), -1003)

  def test_a_value_with_float_error_is_rounded_to_the_tenth_it_means(self):
    self.assertEqual(PipettesConfiguration().to_increments(389.1), 3891)
    self.assertEqual(PipettesConfiguration().to_increments(201.6), 2016)


class TestQueries(PipettesTestCase):
  """What the channels are asked."""

  async def test_the_tips_come_back_as_booleans_one_for_each_channel(self):
    self.io.answers = machine(tips=[True, False, False, True, False, False, False, False])
    tips = await self.pipettes.request_tip_presence()
    self.assertEqual(tips, [True, False, False, True, False, False, False, False])
    self.assertTrue(all(isinstance(tip, bool) for tip in tips))
    self.assertEqual([without_id(command) for command in self.io.written], ["A1PMQA"])

  async def test_the_initialization_status_is_true_only_for_1(self):
    self.io.answers = machine(channels_initialized=True)
    self.assertTrue(await self.pipettes.request_initialization_status())
    self.io.answers = machine(channels_initialized=False)
    self.assertFalse(await self.pipettes.request_initialization_status())
    self.assertEqual([command[:6] for command in self.io.written], ["A1PMQW", "A1PMQW"])


class TestInitialize(PipettesTestCase):
  """Initializing the channels, which moves them."""

  async def test_by_default_the_wire_carries_what_the_legacy_backend_sends_on_setup(self):
    legacy = LegacyCatcher(self.num_channels)
    await legacy.pip_initialize(
      x_position=[7095] * 8,
      y_position=[3891, 3623, 3355, 3087, 2819, 2551, 2283, 2016],
      begin_z_deposit_position=[2450] * 8,
      end_z_deposit_position=[1235] * 8,
      minimal_height_at_command_end=[2450] * 8,
      tip_pattern=[True] * 8,
      tip_type=[1] * 8,
      TODO_DI_2=70,
    )
    await self.pipettes.initialize(tip_type=[1] * 8)
    self.assertEqual(without_id(self.io.written[0]), without_id(legacy.commands[0]))

  async def test_the_defaults_for_eight_channels(self):
    await self.pipettes.initialize()
    self.assertEqual(
      without_id(self.io.written[0]),
      "A1PMDI"
      "xp"
      + " ".join(["7095"] * 8)
      + "yp3891 3623 3355 3087 2819 2551 2283 2016"
      + "tp"
      + " ".join(["2450"] * 8)
      + "tz"
      + " ".join(["1235"] * 8)
      + "te"
      + " ".join(["2450"] * 8)
      + "tm"
      + " ".join(["1"] * 8)
      + "tt"
      + " ".join(["4"] * 8)
      + "ts70",
    )

  async def test_the_command_goes_to_the_channels_and_is_given_the_time_they_need(self):
    seen = {}
    send = self.driver._router.send

    async def spying(cmd, id_, write_timeout=None, read_timeout=None, wait=True):
      seen[cmd[:6]] = read_timeout
      return await send(cmd, id_, write_timeout, read_timeout, wait)

    self.driver._router.send = spying  # type: ignore[method-assign]
    await self.pipettes.initialize()
    self.assertEqual(seen, {"A1PMDI": 300})

  async def test_what_is_given_is_taken_in_mm_and_converted(self):
    await self.pipettes.initialize(
      x_position=[100.5] * 8,
      y_positions=[10.0 * (i + 1) for i in range(8)],
      begin_of_tip_deposit=[200.0] * 8,
      end_of_tip_deposit=[100.3] * 8,
      minimal_height_at_command_end=[300.0] * 8,
      tip_type=[0, 1, 2, 3, 4, 5, 6, 7],
      x_shift_during_discard=-2.5,
    )
    command = without_id(self.io.written[0])
    self.assertIn("xp" + " ".join(["1005"] * 8), command)
    self.assertIn("yp100 200 300 400 500 600 700 800", command)
    self.assertIn("tp" + " ".join(["2000"] * 8), command)
    self.assertIn("tz" + " ".join(["1003"] * 8), command)
    self.assertIn("te" + " ".join(["3000"] * 8), command)
    self.assertIn("tt0 1 2 3 4 5 6 7", command)
    self.assertTrue(command.endswith("ts-25"), command)

  async def test_channels_can_be_left_out(self):
    await self.pipettes.initialize(tip_pattern=[True] + [False] * 7)
    self.assertIn("tm1 0 0 0 0 0 0 0", without_id(self.io.written[0]))

  async def test_a_list_that_does_not_hold_one_value_for_each_channel_is_refused(self):
    for name in (
      "x_position",
      "y_positions",
      "begin_of_tip_deposit",
      "end_of_tip_deposit",
      "minimal_height_at_command_end",
      "tip_pattern",
      "tip_type",
    ):
      with self.assertRaises(ValueError, msg=name) as caught:
        await self.pipettes.initialize(**{name: [1] * 7})  # type: ignore[arg-type]
      self.assertIn(name, str(caught.exception))
    self.assertEqual(self.io.written, [])

  async def test_a_value_out_of_range_is_refused_and_nothing_is_sent(self):
    refused = (
      {"x_position": [-0.1] * 8},
      {"x_position": [5000.1] * 8},
      {"y_positions": [-0.1] * 8},
      {"y_positions": [650.1] * 8},
      {"begin_of_tip_deposit": [360.1] * 8},
      {"end_of_tip_deposit": [-1.0] * 8},
      {"minimal_height_at_command_end": [361.0] * 8},
      {"tip_type": [200] * 8},
      {"tip_type": [-1] * 8},
      {"x_shift_during_discard": 100.1},
      {"x_shift_during_discard": -100.1},
    )
    for arguments in refused:
      with self.assertRaises(ValueError, msg=str(arguments)):
        await self.pipettes.initialize(**arguments)
    self.assertEqual(self.io.written, [])

  async def test_the_ends_of_every_range_are_accepted(self):
    await self.pipettes.initialize(
      x_position=[0.0] * 4 + [5000.0] * 4,
      y_positions=[0.0] * 4 + [650.0] * 4,
      begin_of_tip_deposit=[0.0] * 4 + [360.0] * 4,
      tip_type=[0] * 4 + [199] * 4,
      x_shift_during_discard=100.0,
    )
    await self.pipettes.initialize(x_shift_during_discard=-100.0)
    self.assertEqual(len(self.io.written), 2)

  async def test_a_reported_error_reaches_the_caller(self):
    self.io.answers = lambda command: [echo(command, 'es"P170"', error=99)]
    with self.assertRaises(VantageFirmwareError):
      await self.pipettes.initialize()


class TestAMachineWithAnotherChannelCount(PipettesTestCase):
  num_channels = 4

  async def test_there_is_no_default_discard_position_for_it(self):
    with self.assertRaises(ValueError) as caught:
      await self.pipettes.initialize()
    self.assertIn("y_positions", str(caught.exception))
    self.assertIn("4 channels", str(caught.exception))
    self.assertEqual(self.io.written, [])

  async def test_it_initializes_where_it_is_told_to(self):
    await self.pipettes.initialize(y_positions=[400.0, 350.0, 300.0, 250.0])
    command = without_id(self.io.written[0])
    self.assertIn("yp4000 3500 3000 2500", command)
    self.assertIn("xp7095 7095 7095 7095", command)
    self.assertIn("tm1 1 1 1", command)
    self.assertNotIn("&", command)

  async def test_the_defaults_are_a_public_table_that_can_be_extended(self):
    self.pipettes.default_y_positions = {4: [400.0, 350.0, 300.0, 250.0]}
    await self.pipettes.initialize()
    self.assertIn("yp4000 3500 3000 2500", self.io.written[0])


class TestBeforeDiscovery(unittest.TestCase):
  def test_the_number_of_channels_is_not_known(self):
    pipettes = Pipettes(VantageDriver(ScriptedIO()))
    with self.assertRaises(RuntimeError):
      pipettes.num_channels  # noqa: B018


if __name__ == "__main__":
  unittest.main()


def deck_with_tips(track: int = 15, site: int = 1) -> Tuple[VantageDeck, TipRack]:
  """A 1.3 m deck with a tip carrier on a track, and a rack of 300 uL tips in one of its sites.

  The rack is put into its site without a location: a holder centres a rack over the opening it
  sinks into, and sinks it by the rack's own sinking depth. Placing it at the holder's corner
  instead would put it about 4 mm off in X and Y, and 6 mm too high.
  """
  deck = VantageDeck(size=1.3, name="deck")
  carrier = hamilton_tip_carrier_L5("tip_carrier")
  deck.assign_child_resource(carrier, track=track)
  rack = hamilton_96_tiprack_300uL("rack")
  carrier[site].assign_child_resource(rack)
  return deck, rack


class TipsTestCase(unittest.IsolatedAsyncioTestCase):
  """A driver with a deck, a rack of 300 uL tips, and the machine discovered."""

  async def asyncSetUp(self):
    self.deck, self.rack = deck_with_tips()
    self.io = ScriptedIO(machine())
    self.driver = VantageDriver(self.io, deck=self.deck, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()
    await self.driver.discover()
    self.io.written.clear()
    pipettes = self.driver.pipettes
    assert pipettes is not None
    self.pipettes: Pipettes = pipettes
    self.spots = self.rack.get_items("A1:H1")

  async def asyncTearDown(self):
    self.driver._router.stop()

  def sent(self) -> List[str]:
    return [without_id(command) for command in self.io.written]

  def command(self, module_and_command: str) -> str:
    """The one command with that module and command."""
    found = [command for command in self.sent() if command.startswith(module_and_command)]
    self.assertEqual(len(found), 1, self.sent())
    return found[0]

  def parameter(self, command: str, key: str) -> str:
    """The value of one parameter of a command, as it was written."""
    found = re.search(rf"{key}([^a-z]+)", command[6:])
    assert found is not None, (key, command)
    return found.group(1).strip()


class TestPickUpTips(TipsTestCase):
  """Picking tips up, which moves the channels."""

  def where(self) -> Tuple[List[int], List[int], int, int]:
    """The wire positions of the spots, and where the pickup begins and ends, in 0.1 mm."""
    config = PipettesConfiguration()
    xs: List[int] = []
    ys: List[int] = []
    for spot in self.spots:
      location = spot.get_location_wrt(self.deck, x="c", y="c", z="b")
      xs.append(config.to_increments(location.x))
      ys.append(config.to_increments(location.y))
    z = self.spots[0].get_location_wrt(self.deck, z="b").z
    return xs, ys, config.to_increments(z + 8.0), config.to_increments(z)

  async def test_the_heights_are_the_ones_legacy_was_tested_with(self):
    # The same deck, carrier, rack and tips as the legacy tests: a 300 uL tip in its rack.
    await self.pipettes.pick_up_tips(self.spots[:1], use_channels=[0])
    command = self.command("A1PMTP")
    self.assertEqual(self.parameter(command, "tp").split(), ["2244"] + ["0"] * 7)
    self.assertEqual(self.parameter(command, "tz").split(), ["2164"] + ["0"] * 7)

  async def test_the_wire_is_what_legacy_sends(self):
    xs, ys, begin, end = self.where()
    legacy = LegacyCatcher(self.pipettes.num_channels)
    await legacy.pip_tip_pick_up(
      x_position=xs,
      y_position=ys,
      tip_pattern=[True] * 8,
      tip_type=[0] * 8,
      begin_z_deposit_position=[begin] * 8,
      end_z_deposit_position=[end] * 8,
      minimal_traverse_height_at_begin_of_command=[2450] * 8,
      minimal_height_at_command_end=[2450] * 8,
      blow_out_air_volume=[0] * 8,
      tip_handling_method=[1] * 8,
    )
    await self.pipettes.pick_up_tips(self.spots)
    for key in ("xp", "yp", "tm", "tt", "tp", "tz", "th", "te", "ba", "td"):
      self.assertEqual(
        self.parameter(self.command("A1PMTP"), key),
        self.parameter(legacy.commands[0], key),
        key,
      )

  async def test_a_plain_300_ul_tip_needs_no_tip_type_written(self):
    await self.pipettes.pick_up_tips(self.spots)
    self.assertEqual([command[:6] for command in self.sent()], ["A1PMTP"])
    self.assertEqual(self.parameter(self.command("A1PMTP"), "tt"), " ".join(["0"] * 8))

  async def test_the_entry_is_remembered_between_commands(self):
    await self.pipettes.pick_up_tips(self.spots)
    await self.pipettes.drop_tips(self.spots)
    self.io.written.clear()
    await self.pipettes.pick_up_tips(self.spots)
    self.assertEqual([command[:6] for command in self.sent()], ["A1PMTP"])

  async def test_the_pickup_ends_at_the_spot_and_begins_a_collar_above_it(self):
    _, _, begin, end = self.where()
    await self.pipettes.pick_up_tips(self.spots)
    command = self.command("A1PMTP")
    self.assertEqual(self.parameter(command, "tz"), " ".join([str(end)] * 8))
    self.assertEqual(self.parameter(command, "tp"), " ".join([str(begin)] * 8))
    self.assertEqual(self.parameter(command, "th"), " ".join(["2450"] * 8))
    self.assertEqual(self.parameter(command, "te"), " ".join(["2450"] * 8))
    self.assertEqual(self.parameter(command, "td"), " ".join(["1"] * 8))
    self.assertEqual(self.parameter(command, "ba"), " ".join(["0"] * 8))

  async def test_where_the_pickup_begins_and_ends_can_be_given(self):
    await self.pipettes.pick_up_tips(
      self.spots,
      begin_tip_pick_up_process=240.0,
      end_tip_pick_up_process=230.0,
      minimum_traverse_height_start=300.0,
    )
    command = self.command("A1PMTP")
    self.assertEqual(self.parameter(command, "tp"), " ".join(["2400"] * 8))
    self.assertEqual(self.parameter(command, "tz"), " ".join(["2300"] * 8))
    self.assertEqual(self.parameter(command, "th"), " ".join(["3000"] * 8))
    self.assertEqual(self.parameter(command, "te"), " ".join(["3000"] * 8))

  async def test_a_traverse_height_at_or_below_the_safety_height_is_refused(self):
    with self.assertRaises(ValueError):
      await self.pipettes.pick_up_tips(self.spots, minimum_traverse_height_start=200.0)
    self.assertEqual(self.io.written, [])

  async def test_a_tip_collected_out_of_wash_liquid_says_so(self):
    set_tip_tracking(True)
    try:
      for spot in self.spots:
        assert isinstance(spot.tip, HamiltonTip)
        spot.tip.pickup_method = TipPickupMethod.OUT_OF_WASH_LIQUID
      await self.pipettes.pick_up_tips(self.spots)
    finally:
      set_tip_tracking(False)
    self.assertEqual(self.parameter(self.command("A1PMTP"), "td"), " ".join(["2"] * 8))

  async def test_the_spots_are_where_the_tips_are(self):
    xs, ys, _, _ = self.where()
    await self.pipettes.pick_up_tips(self.spots)
    command = self.command("A1PMTP")
    self.assertEqual(self.parameter(command, "xp"), " ".join(str(x) for x in xs))
    self.assertEqual(self.parameter(command, "yp"), " ".join(str(y) for y in ys))

  async def test_the_tips_leave_their_spots_and_get_onto_their_channels(self):
    await self.pipettes.pick_up_tips(self.spots)
    for channel, spot in enumerate(self.spots):
      self.assertIsNone(spot.tip, spot.name)
      mounted = self.pipettes.get_mounted_tip(channel)
      self.assertIsNotNone(mounted, channel)
      self.assertEqual(mounted.name.split("#")[0], spot.name)  # type: ignore[union-attr]

  async def test_a_channel_that_already_carries_a_tip_is_refused(self):
    await self.pipettes.pick_up_tips(self.spots)
    self.io.written.clear()
    with self.assertRaises(HasTipError):
      await self.pipettes.pick_up_tips(self.spots)
    self.assertEqual(self.io.written, [])

  async def test_a_spot_that_holds_no_tip_is_refused_while_tips_are_tracked(self):
    set_tip_tracking(True)
    try:
      self.spots[0].unassign_tip()
      with self.assertRaises(NoTipError):
        await self.pipettes.pick_up_tips(self.spots)
    finally:
      set_tip_tracking(False)
    self.assertEqual(self.io.written, [])

  async def test_channels_can_be_given_and_are_ascending(self):
    await self.pipettes.pick_up_tips(self.spots[:2], use_channels=[0, 1])
    self.assertIsNotNone(self.pipettes.get_mounted_tip(0))
    self.assertIsNotNone(self.pipettes.get_mounted_tip(1))
    self.assertIsNone(self.pipettes.get_mounted_tip(2))

  async def test_the_channels_that_take_no_part_are_written_out_as_zero(self):
    # A `&` after a short list means "the last value again", so it would take every remaining
    # channel to the last position and pick up a tip there.
    xs, ys, _, _ = self.where()
    await self.pipettes.pick_up_tips(self.spots[:2], use_channels=[0, 1])
    command = self.command("A1PMTP")
    self.assertNotIn("&", command)
    self.assertEqual(self.parameter(command, "tm"), "1 1 0 0 0 0 0 0")
    self.assertEqual(self.parameter(command, "xp"), " ".join(str(x) for x in xs[:2] + [0] * 6))
    self.assertEqual(self.parameter(command, "yp"), " ".join(str(y) for y in ys[:2] + [0] * 6))

  async def test_channels_that_are_not_ascending_are_refused(self):
    with self.assertRaises(ValueError):
      await self.pipettes.pick_up_tips(self.spots[:2], use_channels=[1, 0])

  async def test_two_tips_closer_than_the_channels_stand_are_taken_but_warned_about(self):
    with self.assertLogs(PIPETTES_LOGGER, "WARNING") as logs:
      await self.pipettes.pick_up_tips(
        self.spots[:2], use_channels=[0, 1], offsets=[Coordinate(0, 0, 0), Coordinate(0, 0.5, 0)]
      )
    self.assertIn("8.5 mm", logs.output[0])
    self.assertIn("9.0 mm", logs.output[0])
    self.assertIsNotNone(self.pipettes.get_mounted_tip(1))

  async def test_a_tenth_below_the_minimum_is_already_too_close(self):
    with self.assertLogs(PIPETTES_LOGGER, "WARNING") as logs:
      await self.pipettes.pick_up_tips(
        self.spots[:2], use_channels=[0, 1], offsets=[Coordinate(0, 0, 0), Coordinate(0, 0.1, 0)]
      )
    self.assertIn("8.9 mm", logs.output[0])

  async def test_tips_at_the_minimum_spacing_are_taken_without_a_word(self):
    with self.assertNoLogs(PIPETTES_LOGGER, "WARNING"):
      await self.pipettes.pick_up_tips(self.spots[:2], use_channels=[0, 1])
    self.assertIsNotNone(self.pipettes.get_mounted_tip(1))

  async def test_tips_a_column_apart_may_be_any_distance_in_y_without_a_word(self):
    with self.assertNoLogs(PIPETTES_LOGGER, "WARNING"):
      await self.pipettes.pick_up_tips(
        self.spots[:2], use_channels=[0, 1], offsets=[Coordinate(0, 0, 0), Coordinate(9.0, 1.0, 0)]
      )
    self.assertIsNotNone(self.pipettes.get_mounted_tip(1))

  async def test_a_position_the_channels_cannot_reach_is_refused(self):
    with self.assertRaises(ValueError):
      await self.pipettes.pick_up_tips(self.spots, offsets=[Coordinate(6000.0, 0.0, 0.0)] * 8)
    self.assertEqual(self.io.written, [])

  async def test_a_failed_pickup_takes_the_machines_word_for_what_came_up(self):
    def answers(command: str) -> Optional[List[str]]:
      if command.startswith("A1PMTP"):
        return [echo(command, 'es"P123"', error=1)]
      if command.startswith("A1PMQA"):
        return [echo(command, "rt1 0 0 0 0 0 0 0")]
      return machine()(command)

    self.io.answers = answers
    with self.assertRaises(VantageFirmwareError):
      await self.pipettes.pick_up_tips(self.spots)
    self.assertIsNotNone(self.pipettes.get_mounted_tip(0))
    self.assertIsNone(self.spots[0].tip)
    self.assertIsNone(self.pipettes.get_mounted_tip(1))
    self.assertIsNotNone(self.spots[1].tip)


class TestDropTips(TipsTestCase):
  """Putting tips back into their spots."""

  async def asyncSetUp(self):
    await super().asyncSetUp()
    await self.pipettes.pick_up_tips(self.spots)
    self.io.written.clear()

  def where(self) -> Tuple[List[int], List[int]]:
    config = PipettesConfiguration()
    xs: List[int] = []
    ys: List[int] = []
    for spot in self.spots:
      location = spot.get_location_wrt(self.deck, x="c", y="c", z="b")
      xs.append(config.to_increments(location.x))
      ys.append(config.to_increments(location.y))
    return xs, ys

  def end(self) -> int:
    """Where the tip's end sits in its spot, in 0.1 mm."""
    seat = self.spots[0].get_location_wrt(self.deck, z="b").z
    return PipettesConfiguration().to_increments(seat - (59.9 - 8.0))

  async def test_the_heights_are_the_ones_legacy_was_tested_with(self):
    await self.pipettes.drop_tips(self.spots[:1], use_channels=[0])
    command = self.command("A1PMTR")
    self.assertEqual(self.parameter(command, "tp").split(), ["1745"] + ["0"] * 7)
    self.assertEqual(self.parameter(command, "tz").split(), ["1645"] + ["0"] * 7)

  async def test_the_wire_is_what_legacy_sends(self):
    xs, ys = self.where()
    end = self.end()
    legacy = LegacyCatcher(self.pipettes.num_channels)
    await legacy.pip_tip_discard(
      x_position=xs,
      y_position=ys,
      begin_z_deposit_position=[end + 100] * 8,
      end_z_deposit_position=[end] * 8,
      minimal_traverse_height_at_begin_of_command=[2450] * 8,
      minimal_height_at_command_end=[2450] * 8,
      tip_pattern=[True] * 8,
      TODO_TR_2=0,
      tip_handling_method=[0] * 8,
    )
    await self.pipettes.drop_tips(self.spots)
    for key in ("xp", "yp", "tp", "tz", "th", "te", "tm", "ts", "td"):
      self.assertEqual(
        self.parameter(self.command("A1PMTR"), key),
        self.parameter(legacy.commands[0], key),
        key,
      )

  async def test_the_tips_go_back_into_their_spots(self):
    await self.pipettes.drop_tips(self.spots)
    for channel, spot in enumerate(self.spots):
      self.assertIsNotNone(spot.tip, spot.name)
      self.assertIsNone(self.pipettes.get_mounted_tip(channel))
    self.assertEqual([command[:6] for command in self.sent()], ["A1PMTR"])

  async def test_a_channel_without_a_tip_is_refused(self):
    await self.pipettes.drop_tips(self.spots[:1], use_channels=[0])
    with self.assertRaises(ValueError):
      await self.pipettes.drop_tips(self.spots[:1], use_channels=[0])

  async def test_a_spot_that_holds_a_tip_already_is_refused(self):
    await self.pipettes.drop_tips(self.spots[:1], use_channels=[0])
    self.io.written.clear()
    with self.assertRaises(HasTipError):
      await self.pipettes.drop_tips(self.spots[:1], use_channels=[1])
    self.assertEqual(self.io.written, [])
