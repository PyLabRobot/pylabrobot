import unittest
from typing import Any, List, Optional, Tuple

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes, PipettesConfiguration
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.utils.liquid_handling.pipette_batch_scheduling import plan_batches


async def channels(width: float, positions: List[float]) -> Tuple[Pipettes, List[str]]:
  """The channels of a simulated device, of one width and at known Y positions.

  Both are what the tests vary: the width decides the minimum spacing a pair must keep, and the
  positions are what the device answers `C0 RY` with. Everything else is the driver's own.

  Args:
    width: what every channel reports its width to be, in mm.
    positions: where each channel is along Y, in mm, back to front.

  Returns:
    The feature, and the list its commands are recorded in.
  """
  driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
  await driver.setup()
  pipettes = driver.pipettes
  assert pipettes is not None

  for channel in pipettes.configuration.channels:
    channel.width = width

  sent: List[str] = []
  answer = driver.send_command

  async def recorded(
    module: str,
    command: str,
    fmt: Optional[Any] = None,
    subsystem: Optional[str] = None,
    **kwargs: Any,
  ):
    # `fmt` and `subsystem` are the driver's own, not firmware parameters: taken exactly as
    # `send_command` takes them, so they never reach the assembler.
    sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
    return await answer(module=module, command=command, fmt=fmt, subsystem=subsystem, **kwargs)

  async def reported_positions() -> List[float]:
    return list(positions)

  driver.send_command = recorded  # type: ignore[assignment]
  # The simulated channels answer this from their own model rather than from the wire, so the
  # starting positions are set here, as legacy sets them on its backend.
  pipettes.request_y_positions = reported_positions  # type: ignore[assignment]
  return pipettes, sent


def jy(yp: str) -> str:
  """The Y positioning command carrying `yp`."""
  return assemble_command(module="C0", command="JY", id_=None, yp=yp)


# The two extremes of 160 distinct `C0 JY` payloads recorded across three years of runs: the
# tightest adjacent gap ever commanded, and the frontmost a channel has ever been sent. Both sit
# exactly on a limit the driver enforces, and nothing recorded goes past either.
TIGHTEST_GAP = [529.8, 520.8, 511.8, 502.8, 493.8, 484.8, 459.0, 338.0]
FRONTMOST = [130.0, 100.0, 91.0, 82.0, 73.0, 64.0, 15.0, 6.0]

# What a channel reports its width to be, in mm: `PxVY` answers 194 increments. Not a round
# number, so the rounding up to 0.1 mm is exercised rather than assumed.
REPORTED_WIDTH = 8.9826


class TestPositionInYDirection(unittest.IsolatedAsyncioTestCase):
  """What the channels' minimum spacing does to a Y positioning command."""

  async def test_the_limits_accept_what_the_device_has_been_commanded(self):
    """The driver's minimum spacing and front limit against the extremes of real runs.

    A minimum wider than 9.0 mm fails on the first, a front limit behind 6.0 mm on the second.
    """
    for payload in (TIGHTEST_GAP, FRONTMOST):
      pipettes, sent = await channels(width=REPORTED_WIDTH, positions=payload)
      await pipettes.move_to_y_positions(dict(enumerate(payload)), make_space=False)
      self.assertEqual(sent[-1], jy(" ".join(f"{round(y * 10):04}" for y in payload)))

  async def test_a_gap_that_is_wide_enough_at_9mm_is_refused_at_18mm(self):
    spread = [100.0, 91.0, 82.0, 73.0, 64.0, 55.0, 46.0, 37.0]

    at_9, sent_9 = await channels(width=9.0, positions=spread)
    await at_9.move_to_y_positions(dict(enumerate(spread)), make_space=False)
    self.assertEqual(sent_9[-1], jy("1000 0910 0820 0730 0640 0550 0460 0370"))

    at_18, _ = await channels(width=18.0, positions=spread)
    with self.assertRaises(ValueError):
      await at_18.move_to_y_positions(dict(enumerate(spread)), make_space=False)

  async def test_make_space_moves_the_channel_in_front_by_the_minimum(self):
    # Already 18mm apart, so the reading needs no conforming and only make_space moves anything.
    current = [400.0, 300.0, 200.0, 160.0, 142.0, 124.0, 106.0, 88.0]

    at_9, sent_9 = await channels(width=9.0, positions=current)
    await at_9.move_to_y_positions({3: 150.0}, make_space=True)
    self.assertEqual(sent_9[-1], jy("4000 3000 2000 1500 1410 1240 1060 0880"))

    at_18, sent_18 = await channels(width=18.0, positions=current)
    await at_18.move_to_y_positions({3: 150.0}, make_space=True)
    self.assertEqual(sent_18[-1], jy("4000 3000 2000 1500 1320 1140 0960 0780"))


async def simulated_channels() -> Pipettes:
  """The channels of a simulated device, as setup leaves them.

  Returns:
    The feature.

  Raises:
    RuntimeError: If the simulated device reports no channels.
  """
  driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
  await driver.setup()
  if driver.pipettes is None:
    raise RuntimeError("the simulated device reports no pipetting channels")
  return driver.pipettes


class TestPositionInZDirection(unittest.IsolatedAsyncioTestCase):
  """What the channels' Z window does to a Z positioning command.

  The window is taken from the configuration rather than written out here: what the drive counts
  differs by arm, so a test that named the millimetres would be testing this generation only.
  """

  async def test_a_z_outside_the_window_is_refused_and_one_inside_is_not(self):
    """The floor is the deck surface, so a Z below it would drive a stop disc into the deck."""
    pipettes = await simulated_channels()
    c = pipettes.configuration
    low, high = c.z_range or c.z_range

    for z in (low - 0.1, high + 0.1):
      with self.assertRaises(ValueError):
        await pipettes.move_stop_disc_to_z_position(0, z)

    await pipettes.move_stop_disc_to_z_position(0, round((low + high) / 2, 1))

  async def test_setup_takes_the_ceiling_from_what_the_channels_reached(self):
    """The probe says how high these channels reach, and setup makes that the ceiling. The floor
    is left as it stands: nothing measures how low they go."""
    pipettes = await simulated_channels()
    floor, ceiling = pipettes.configuration.z_range

    self.assertEqual(ceiling, min((await pipettes.probe_z_max()).values()))
    self.assertEqual(floor, PipettesConfiguration().z_range[0])

  async def test_probing_reads_the_channels_and_changes_nothing(self):
    """It is called for the raise as much as for the reading, so it leaves the window alone: what
    is done with what it read is setup's to decide."""
    pipettes = await simulated_channels()
    floor, _ = pipettes.configuration.z_range
    # A window that is not the one probing would arrive at, so a probe that set it would show.
    pipettes.configuration.z_range = (floor + 10.0, 300.0)

    reached = await pipettes.probe_z_max()

    self.assertEqual(pipettes.configuration.z_range, (floor + 10.0, 300.0))
    self.assertEqual(len(reached), len(pipettes.configuration.channels))


class TestBatchPlanning(unittest.IsolatedAsyncioTestCase):
  """A v1 device plans with `pylabrobot.utils.liquid_handling`, from its own minimum channel spacing."""

  async def test_one_column_is_one_move_and_two_columns_are_two(self):
    """Four wells down one column fit one X/Y move at the channels' spacing; spread across columns they do not."""
    deck = STARDeck()
    plate = cor_axy_96_wellplate_500uL_Ub("plate")
    deck.assign_child_resource(plate, location=Coordinate(400, 100, 100))
    driver = STARSimulationDriver(deck=deck, declared_configuration_json=RECORDING_STAR)
    await driver.setup()
    pipettes = driver.pipettes
    assert pipettes is not None
    gap = pipettes._min_spacing_between(0, 1)
    spacings = [gap] * pipettes.num_channels

    one_column = plan_batches(
      use_channels=[0, 1, 2, 3],
      containers=[plate.get_well(name) for name in ("A1", "B1", "C1", "D1")],
      channel_spacings=spacings,
      wrt_resource=deck,
      x_tolerance=0.1,
    )
    self.assertEqual(len(one_column), 1)
    ys = one_column[0].y_positions
    for back, front in ((0, 1), (1, 2), (2, 3)):
      self.assertGreaterEqual(ys[back] - ys[front], gap - 1e-6)

    two_columns = plan_batches(
      use_channels=[0, 1],
      containers=[plate.get_well("A1"), plate.get_well("A2")],
      channel_spacings=spacings,
      wrt_resource=deck,
      x_tolerance=0.1,
    )
    self.assertEqual(len(two_columns), 2)
    await driver.stop()


class TestWhatTheChannelsCarry(unittest.IsolatedAsyncioTestCase):
  """A simulated channel answers for the tip on its mounting shaft.

  The master reports the bottom of what a channel carries and the channel reports its stop disc, so
  a tip on a shaft has to show up in both reads, a stop disc apart, the way it does on a device.
  """

  async def asyncSetUp(self):
    from pylabrobot.resources.hamilton import hamilton_tip_300uL
    from pylabrobot.resources.n_channel_pipettes import TipMountingShaft

    self.pipettes = await simulated_channels()
    self.shaft = next(
      child for child in self.pipettes.resources[0].children if isinstance(child, TipMountingShaft)
    )
    self.tip = hamilton_tip_300uL(name="tip")

  async def test_a_tip_on_a_shaft_is_sensed_on_that_channel_only(self):
    self.assertEqual(await self.pipettes.sense_tip_presence(), [0] * self.pipettes.num_channels)
    self.shaft.mount_tip(self.tip)
    presence = await self.pipettes.sense_tip_presence()
    self.assertEqual(presence[0], 1)
    self.assertEqual(presence[1:], [0] * (self.pipettes.num_channels - 1))

  async def test_the_overhang_is_how_far_the_tip_reaches_below_the_channel(self):
    """Part of a tip is up inside the channel, so the overhang is its length less its fitting."""
    self.shaft.mount_tip(self.tip)
    overhang = await self.pipettes.request_tip_overhang(0)
    self.assertAlmostEqual(overhang, self.tip.total_tip_length - self.tip.fitting_depth, places=1)

  async def test_the_grip_tool_reaches_to_its_grip_line_as_the_firmware_counts_it(self):
    """The firmware counts the grip tool to its grip line, 30 mm, not to its 32 mm bottom."""
    from pylabrobot.resources.hamilton import hamilton_core_gripper_tool

    self.shaft.mount_tip(hamilton_core_gripper_tool(name="grip"))
    self.assertAlmostEqual(await self.pipettes.request_tip_overhang(0), 30.0 - 8.0, places=1)

  async def test_moving_the_tip_end_puts_the_stop_disc_an_overhang_higher(self):
    self.shaft.mount_tip(self.tip)
    low, high = self.pipettes.configuration.z_range
    z = (low + high) / 2 - 20
    await self.pipettes.move_tool_bottom_to_z_positions({0: z})
    stop_disc = await self.pipettes.request_stop_disc_z_position(0)
    self.assertAlmostEqual(
      stop_disc - z, self.tip.total_tip_length - self.tip.fitting_depth, places=1
    )


async def channels_over_a_rack() -> Tuple[Pipettes, Any, List[str]]:
  """Simulated channels, a 300 uL rack on track 16, and every tip command sent from here on.

  Returns:
    The feature, the rack, and the `C0 TT`, `TP` and `TR` commands as sent, without their ids.
  """
  from pylabrobot.resources.hamilton import TIP_CAR_480_A00, hamilton_96_tiprack_300uL

  pipettes = await simulated_channels()
  deck = pipettes._driver.deck
  assert deck is not None
  carrier = TIP_CAR_480_A00(name="tip_carrier")
  carrier[0] = rack = hamilton_96_tiprack_300uL(name="rack")
  deck.assign_child_resource(carrier, track=16)

  sent: List[str] = []
  log = pipettes._driver._log_exchange  # type: ignore[attr-defined]

  def recorded(written: str, read: Optional[str]) -> None:
    if written[:4] in ("C0TT", "C0TP", "C0TR"):
      sent.append(written)
    log(written, read)

  pipettes._driver._log_exchange = recorded  # type: ignore[attr-defined]
  return pipettes, rack, sent


class TestTipHandling(unittest.IsolatedAsyncioTestCase):
  """A tip is a resource: picked up, it moves from its spot onto the channel's shaft, and back.

  With tip tracking on, which is what makes a spot give up its tip.
  """

  def setUp(self):
    from pylabrobot.resources import set_tip_tracking

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)

  async def test_a_pickup_sends_what_legacy_sends(self):
    pipettes, rack, sent = await channels_over_a_rack()
    await pipettes.pick_up_tips([rack.get_item("A1"), rack.get_item("B1")])
    self.assertEqual(
      sent,
      [
        "C0TTtt01tf0tl0519tv04000tg2tu0",
        "C0TPxp04554 04554 00000&yp1458 1368 0000&tm1 1 0&tt01tp2244tz2164th2450td0",
      ],
    )

  async def test_a_picked_up_tip_is_on_the_shaft_and_not_in_the_spot(self):
    pipettes, rack, _ = await channels_over_a_rack()
    spot = rack.get_item("A1")
    tip = spot.tip
    await pipettes.pick_up_tips([spot])
    self.assertIs(pipettes.get_mounted_tip(0), tip)
    self.assertIs(tip.parent, pipettes.shaft(0))
    self.assertIsNone(spot.tip)
    self.assertEqual((await pipettes.sense_tip_presence())[0], 1)

  async def test_the_tip_bottom_is_its_overhang_below_the_stop_disc(self):
    pipettes, rack, _ = await channels_over_a_rack()
    tip = rack.get_item("A1").tip
    await pipettes.pick_up_tips([rack.get_item("A1")])
    bottom = await pipettes.request_tool_bottom_z_position(0)
    stop_disc = await pipettes.request_stop_disc_z_position(0)
    self.assertAlmostEqual(stop_disc - bottom, tip.total_tip_length - tip.fitting_depth, places=1)

  async def test_a_returned_tip_is_back_in_its_spot(self):
    pipettes, rack, sent = await channels_over_a_rack()
    spot = rack.get_item("A1")
    tip = spot.tip
    await pipettes.pick_up_tips([spot])
    await pipettes.return_tips()
    self.assertIs(tip.parent, spot)
    self.assertIsNotNone(spot.tip)
    self.assertIsNone(pipettes.get_mounted_tip(0))
    self.assertEqual(sent[-1], "C0TRxp04554 00000&yp1458 0000&tm1 0&tp2244tz2164th2450te2450ti1")

  async def test_a_tip_moved_to_another_channel_returns_to_its_own_spot(self):
    """Where a tip goes back to is the tip's, not the channel's: it follows the tip across."""
    pipettes, rack, sent = await channels_over_a_rack()
    spot = rack.get_item("A1")
    tip = spot.tip
    await pipettes.pick_up_tips([spot])
    pipettes.shaft(1).mount_tip(tip)
    await pipettes.return_tips()
    self.assertIs(tip.parent, spot)
    self.assertEqual(
      sent[-1], "C0TRxp00000 04554 00000&yp0000 1458 0000&tm0 1 0&tp2244tz2164th2450te2450ti1"
    )

  async def test_returning_only_some_channels_leaves_the_others_carrying(self):
    pipettes, rack, _ = await channels_over_a_rack()
    spots = [rack.get_item("A1"), rack.get_item("B1")]
    tips = [spot.tip for spot in spots]
    await pipettes.pick_up_tips(spots)
    await pipettes.return_tips(use_channels=[1])
    self.assertIs(tips[1].parent, spots[1])
    self.assertIs(pipettes.get_mounted_tip(0), tips[0])

  async def test_returning_with_no_tips_or_a_tip_from_no_spot_is_refused(self):
    from pylabrobot.resources.hamilton import hamilton_tip_300uL

    pipettes, _, _ = await channels_over_a_rack()
    with self.assertRaises(RuntimeError):
      await pipettes.return_tips()
    pipettes.shaft(0).mount_tip(hamilton_tip_300uL(name="loose"))
    with self.assertRaises(RuntimeError):
      await pipettes.return_tips()

  async def test_a_discarded_tip_belongs_to_nothing(self):
    pipettes, rack, sent = await channels_over_a_rack()
    tip = rack.get_item("A1").tip
    await pipettes.pick_up_tips([rack.get_item("A1")])
    await pipettes.discard_tips()
    self.assertIsNone(tip.parent)
    self.assertIsNone(pipettes.get_mounted_tip(0))
    self.assertTrue(sent[-1].startswith("C0TR") and sent[-1].endswith("ti0"))

  async def test_channels_that_are_not_neighbours_discard_as_legacy_does(self):
    """Three tips on channels 0, 2 and 5 are packed 9 mm apart in the waste, as legacy packs them.

    Channel 1 cannot fit between 0 and 2 there, and the firmware arranges that, so the command is
    sent rather than refused: each pair taking part is checked by itself, as legacy checks it.
    """
    pipettes, rack, sent = await channels_over_a_rack()
    spots = [rack.get_item(w) for w in ("A1", "C1", "F1")]
    await pipettes.pick_up_tips(spots, use_channels=[0, 2, 5])
    await pipettes.discard_tips()
    self.assertEqual(
      sent[-1],
      "C0TRxp13400 00000 13400 00000 00000 13400 00000&yp3202 0000 3112 0000 0000 3022 0000"
      "&tm1 0 1 0 0 1 0&tp1970tz1870th2450te2450ti0",
    )

  async def test_two_channels_closer_than_the_wider_of_them_are_refused(self):
    pipettes, rack, _ = await channels_over_a_rack()
    spots = [rack.get_item("A1"), rack.get_item("B1")]
    with self.assertRaises(ValueError):
      await pipettes.pick_up_tips(
        spots, use_channels=[0, 3], offsets=[Coordinate.zero(), Coordinate(y=4)]
      )

  async def test_a_channel_carrying_a_tip_is_refused_another(self):
    from pylabrobot.resources.errors import HasTipError

    pipettes, rack, _ = await channels_over_a_rack()
    await pipettes.pick_up_tips([rack.get_item("A1")])
    with self.assertRaises(HasTipError):
      await pipettes.pick_up_tips([rack.get_item("B1")])

  async def test_a_failed_pickup_moves_only_the_tips_the_channels_sense(self):
    from unittest.mock import AsyncMock, patch

    pipettes, rack, _ = await channels_over_a_rack()
    spots = [rack.get_item("A1"), rack.get_item("B1")]
    tips = [spot.tip for spot in spots]
    with (
      patch.object(pipettes, "_unchecked_fw_pick_up_tips", AsyncMock(side_effect=RuntimeError)),
      patch.object(pipettes, "sense_tip_presence", AsyncMock(return_value=[1, 0] + [0] * 6)),
    ):
      with self.assertRaises(RuntimeError):
        await pipettes.pick_up_tips(spots)
    self.assertIs(pipettes.get_mounted_tip(0), tips[0])
    self.assertIsNone(pipettes.get_mounted_tip(1))
    self.assertIs(tips[1].parent, spots[1])

  async def test_initialization_leaves_no_tips_on_the_channels(self):
    pipettes, rack, _ = await channels_over_a_rack()
    tip = rack.get_item("A1").tip
    await pipettes.pick_up_tips([rack.get_item("A1")])
    await pipettes.initialize()
    self.assertIsNone(pipettes.get_mounted_tip(0))
    self.assertIsNone(tip.parent)


class TestTipHandlingUntracked(unittest.IsolatedAsyncioTestCase):
  """With tip tracking off a spot is left as it is: a channel collects a fresh tip from it."""

  async def test_an_untracked_spot_keeps_its_tip(self):
    pipettes, rack, _ = await channels_over_a_rack()
    spot = rack.get_item("A1")
    await pipettes.pick_up_tips([spot])
    mounted = pipettes.get_mounted_tip(0)
    self.assertIsNotNone(mounted)
    self.assertIsNotNone(spot.tip)
    self.assertIsNot(spot.tip, mounted)
    await pipettes.return_tips()
    self.assertIsNone(pipettes.get_mounted_tip(0))
    self.assertEqual(len(spot.children), 1)


class TestNestedTipRacksGroundTruth(unittest.IsolatedAsyncioTestCase):
  """The commands for Hamilton's nested tip racks are what Hamilton's own software sends.

  Each rack on an NTR4 module on an MFX carrier and on an NTR carrier, as recorded, but for `td`:
  Hamilton's software sends `td1`, and PyLabRobot sends `td0`, letting the firmware take the pick-up
  process from the tip type. Tip tracking is on, so each tip goes back into its spot.
  """

  def setUp(self):
    from pylabrobot.resources import set_tip_tracking

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)

  async def test_nested_tip_racks_ground_truth(self):
    from pylabrobot.resources.hamilton import (
      hamilton_96_tiprack_10uL_NTR,
      hamilton_96_tiprack_50uL_NTR,
      hamilton_96_tiprack_300uL_NTR,
      hamilton_mfx_carrier_L5_base,
      hamilton_mfx_module_tiprackholder_ntr,
      hamilton_tip_carrier_L5_ntr_a00,
    )

    # rack, tip definition (without its index), pick-up tp, drop tp/tz (stop disc, ti1), NTR site
    tips = {
      "10uL": (hamilton_96_tiprack_10uL_NTR, "tf0tl0219tv00150tg1tu0", "1900", "tp1900tz1820", 0),
      "50uL": (hamilton_96_tiprack_50uL_NTR, "tf0tl0424tv00650tg2tu0", "1920", "tp1920tz1840", 1),
      "300uL": (hamilton_96_tiprack_300uL_NTR, "tf0tl0519tv04000tg2tu0", "1920", "tp1920tz1840", 2),
    }
    # holder, A1 x (0.1 mm), A1 y (0.1 mm) per rack
    holders = {
      "ntr4_module": ("09505", {"10uL": 4340, "50uL": 4340, "300uL": 4340}),
      "ntr_carrier": ("07704", {"10uL": 1458, "50uL": 2418, "300uL": 3378}),
    }

    for holder_name, (xs, a1_y) in holders.items():
      for size, (rack_fn, definition, tp, drop, site) in tips.items():
        with self.subTest(holder=holder_name, tip=size):
          pipettes, _, sent = await channels_over_a_rack()
          deck = pipettes._driver.deck
          assert deck is not None
          module = hamilton_mfx_module_tiprackholder_ntr("ntr4_module")
          deck.assign_child_resource(
            hamilton_mfx_carrier_L5_base("mfx_carrier", modules={3: module}),
            location=Coordinate(932.5, 63, 100),
          )
          ntr_carrier = hamilton_tip_carrier_L5_ntr_a00("ntr_carrier")
          deck.assign_child_resource(ntr_carrier, location=Coordinate(752.5, 63, 100))
          holder = module if holder_name == "ntr4_module" else ntr_carrier.sites[site]
          rack = rack_fn(f"{holder_name}_{size}")
          holder.assign_child_resource(rack)
          sent.clear()

          spots = rack["A1:H1"]
          await pipettes.pick_up_tips(spots)
          await pipettes.drop_tips(spots)

          tt = sent[0][4:8]
          xp = " ".join([xs] * 8)
          yp = " ".join(f"{a1_y[size] - 90 * row:04}" for row in range(8))
          self.assertEqual(
            sent,
            [
              f"C0TT{tt}{definition}",
              f"C0TPxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{tt}tp{tp}tz1840th2450td0",
              f"C0TRxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{drop}th2450te2450ti1",
            ],
          )
