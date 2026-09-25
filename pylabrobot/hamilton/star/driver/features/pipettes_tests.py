import asyncio
import math
import unittest
import unittest.mock
from typing import Any, List, Literal, Optional, Tuple

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.errors import STARFirmwareError, check_fw_string_error
from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes, PipettesConfiguration
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.hamilton import STARDeck


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


class TestMinimumYSpacings(unittest.IsolatedAsyncioTestCase):
  """What `plan_batches` is told about how close two channels may stand."""

  async def test_every_pair_takes_the_channels_own_width(self):
    pipettes, _ = await channels(width=9.0, positions=TestXYMove.SPREAD)
    self.assertEqual(pipettes.minimum_y_spacings, [9.0] * 7 + [0.0])

  async def test_a_wider_channel_widens_the_pairs_it_is_in(self):
    pipettes, _ = await channels(width=9.0, positions=TestXYMove.SPREAD)
    pipettes.configuration.channels[3].width = 12.0

    self.assertEqual(pipettes.minimum_y_spacings, [9.0, 9.0, 12.0, 12.0, 9.0, 9.0, 9.0, 0.0])

  async def test_a_width_the_device_has_not_reported_is_refused(self):
    pipettes, _ = await channels(width=9.0, positions=TestXYMove.SPREAD)
    pipettes.configuration.channels[0].width = None

    with self.assertRaises(RuntimeError):
      _ = pipettes.minimum_y_spacings


class TestXYMove(unittest.IsolatedAsyncioTestCase):
  """`move_to_xy_positions`: checked first, the low channels up, then X and Y together."""

  SPREAD = [400.0, 300.0, 200.0, 160.0, 142.0, 124.0, 106.0, 88.0]

  async def asyncSetUp(self):
    self.pipettes, self.sent = await channels(width=9.0, positions=self.SPREAD)
    await self.pipettes.move_stop_disc_to_z_positions({0: 200.0, 1: 250.0})
    self.sent.clear()

  def _first(self, prefix: str) -> int:
    return next(i for i, command in enumerate(self.sent) if command.startswith(prefix))

  async def test_only_the_low_channels_rise_and_before_x_and_y(self):
    await self.pipettes.move_to_xy_positions(500.0, {0: 420.0})
    raised = [command[:4] for command in self.sent if command[2:4] == "ZA"]
    self.assertEqual(raised, ["P1ZA"])
    self.assertIn("P1ZAza22838", self.sent[self._first("P1ZA")])
    self.assertLess(self._first("P1ZA"), self._first("X0XP"))
    self.assertLess(self._first("P1ZA"), self._first("C0JY"))

  async def test_a_height_of_0_raises_nothing(self):
    await self.pipettes.move_to_xy_positions(500.0, {0: 420.0}, minimum_traverse_height_start=0)
    self.assertFalse([command for command in self.sent if command[2:4] == "ZA"])
    self.assertTrue(any(command.startswith("X0XP") for command in self.sent))

  async def test_a_refused_x_or_y_moves_nothing(self):
    for x, ys in ((5000.0, {0: 420.0}), (500.0, {1: 399.0})):
      with self.assertRaises(ValueError):
        await self.pipettes.move_to_xy_positions(x, ys)
    moves = [command for command in self.sent if command[2:4] in ("ZA", "XP", "JY")]
    self.assertEqual(moves, [])

  async def test_x_and_y_go_out_together(self):
    """X waits for Y to be sent: moved one after the other, this would time out."""
    recorded = self.pipettes._driver.send_command
    y_sent = asyncio.Event()

    async def x_waits_for_y(module: str, command: str, **kwargs: Any):
      if command == "JY":
        y_sent.set()
      if command == "XP":
        await asyncio.wait_for(y_sent.wait(), timeout=2)
      return await recorded(module=module, command=command, **kwargs)

    self.pipettes._driver.send_command = x_waits_for_y  # type: ignore[assignment]
    await self.pipettes.move_to_xy_positions(500.0, {0: 420.0})
    self.assertTrue(y_sent.is_set())

  async def test_two_low_channels_rise_together_then_x_and_y(self):
    """Both raises go out before either answers: sent one after the other, this would time out."""
    await self.pipettes.move_stop_disc_to_z_positions({1: 220.0})
    self.sent.clear()
    recorded = self.pipettes._driver.send_command
    both_sent = asyncio.Event()
    raising: List[str] = []

    async def raises_wait_for_each_other(module: str, command: str, **kwargs: Any):
      if command == "ZA" and module != "C0":
        raising.append(module)
        if len(raising) == 2:
          both_sent.set()
        await asyncio.wait_for(both_sent.wait(), timeout=2)
      return await recorded(module=module, command=command, **kwargs)

    self.pipettes._driver.send_command = raises_wait_for_each_other  # type: ignore[assignment]
    await self.pipettes.move_to_xy_positions(500.0, {0: 420.0})
    self.assertEqual(raising, ["P1", "P2"])
    moves = [command for command in self.sent if command[2:4] in ("ZA", "XP", "JY")]
    # The raises answer in whichever order the drives finish; X and Y follow both.
    self.assertEqual(
      sorted(moves[:2]), ["P1ZAza22838zv11652zr075zw3", "P2ZAza22838zv11652zr075zw3"]
    )
    self.assertEqual(
      moves[2:], ["X0XPla05000lr3lw7", jy("4200 3000 2000 1600 1420 1240 1060 0880")]
    )

  async def test_a_raise_out_of_reach_moves_nothing(self):
    with self.assertRaises(ValueError):
      await self.pipettes.move_to_xy_positions(500.0, {0: 420.0}, minimum_traverse_height_start=400)
    moves = [command for command in self.sent if command[2:4] in ("ZA", "XP", "JY")]
    self.assertEqual(moves, [])

  async def test_a_failed_x_is_raised_once_y_has_gone_out(self):
    recorded = self.pipettes._driver.send_command

    async def x_fails(module: str, command: str, **kwargs: Any):
      if command == "XP":
        raise RuntimeError("X refused")
      return await recorded(module=module, command=command, **kwargs)

    self.pipettes._driver.send_command = x_fails  # type: ignore[assignment]
    with self.assertRaisesRegex(RuntimeError, "X refused"):
      await self.pipettes.move_to_xy_positions(500.0, {0: 420.0})
    self.assertEqual(self.sent[-1], jy("4200 3000 2000 1600 1420 1240 1060 0880"))


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

    self.assertEqual(ceiling, min(await pipettes.probe_z_max()))
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

  async def test_the_z_reads_answer_a_list_by_channel(self):
    """Each read answers one position per channel, back to front."""
    pipettes = await simulated_channels()
    stop_discs = await pipettes.request_stop_disc_z_positions()
    lowest = await pipettes._unchecked_fw_request_lowest_z_positions()
    for reached in (stop_discs, lowest, await pipettes.probe_z_max()):
      self.assertIsInstance(reached, list)
      self.assertEqual(len(reached), pipettes.num_channels)
    self.assertEqual(stop_discs[3], await pipettes.request_stop_disc_z_position(3))


class TestDriveParameters(unittest.IsolatedAsyncioTestCase):
  """A channel's stored Y/Z speed and acceleration: read with `Px RA`, written with `Px AA`."""

  async def asyncSetUp(self):
    self.pipettes = await simulated_channels()
    self.sent: List[str] = []

    async def recorded(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
      self.sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
      return {"zr": 75, "zv": 12000, "yv": 6000, "yr": 4} if command == "RA" else None

    self.pipettes._driver.send_command = recorded  # type: ignore[assignment]

  async def test_request(self):
    self.assertEqual(await self.pipettes.request_z_acceleration(0), 804.6)
    self.assertEqual(await self.pipettes.request_z_speed(0), 128.73)
    self.assertEqual(self.sent, ["P1RArazr", "P1RArazv"])

  async def test_set(self):
    await self.pipettes._set_z_acceleration(1, 800.0)
    await self.pipettes._set_z_speed(1, 50.0)
    self.assertEqual(self.sent, ["P2AAzr075", "P2AAzv04661"])

  async def test_refused_sends_nothing(self):
    with self.assertRaises(ValueError):
      await self.pipettes._set_drive_parameter(0, "xv", 100.0)
    with self.assertRaises(ValueError):
      await self.pipettes._set_z_acceleration(0, 2000.0)
    with self.assertRaises(ValueError):
      await self.pipettes._set_z_speed(0, 200.0)
    with self.assertRaises(ValueError):
      await self.pipettes._set_z_speed(8, 100.0)
    self.assertEqual(self.sent, [])

  async def test_the_allowed_ranges_are_the_drives_in_mm(self):
    c = self.pipettes.configuration
    self.assertEqual(c.y_speed_range, (0.93, 370.42))
    self.assertEqual(c.z_speed_range, (0.21, 160.91))
    self.assertEqual(c.z_acceleration_range, (53.6, 1609.1))
    self.assertEqual(c.y_drive_acceleration_level_range, (1, 4))

  async def test_profile_sets_then_puts_back_the_defaults(self):
    async with self.pipettes._temporary_z_drive_profile(
      speed=50.0, acceleration=150.0, channels=[7]
    ):
      self.assertEqual(self.sent, ["P8AAzv04661", "P8AAzr014"])
    self.assertEqual(self.sent[2:], ["P8AAzv11652", "P8AAzr075"])

  async def test_profile_puts_back_when_the_block_raises(self):
    with self.assertRaises(RuntimeError):
      async with self.pipettes._temporary_z_drive_profile(acceleration=150.0, channels=[7]):
        raise RuntimeError("the block")
    self.assertEqual(self.sent, ["P8AAzr014", "P8AAzr075"])

  async def test_request_y(self):
    self.assertEqual(await self.pipettes.request_y_speed(7), 277.81)
    self.assertEqual(await self.pipettes.request_y_acceleration_level(7), 4)
    self.assertEqual(self.sent, ["P8RArayv", "P8RArayr"])

  async def test_set_y(self):
    await self.pipettes._set_y_speed(7, 250.0)
    await self.pipettes._set_y_acceleration_level(7, 1)
    self.assertEqual(self.sent, ["P8AAyv5399", "P8AAyr1"])

  async def test_refused_y_sends_nothing(self):
    with self.assertRaises(ValueError):
      await self.pipettes._set_y_acceleration_level(7, 5)
    with self.assertRaises(ValueError):
      await self.pipettes._set_y_speed(7, 400.0)
    self.assertEqual(self.sent, [])

  async def test_y_profile_sets_then_puts_back_the_defaults(self):
    async with self.pipettes._temporary_y_drive_profile(
      speed=46.3, acceleration_level=1, channels=[7]
    ):
      self.assertEqual(self.sent, ["P8AAyv1000", "P8AAyr1"])
    self.assertEqual(self.sent[2:], ["P8AAyv5399", "P8AAyr3"])

  async def test_profile_touches_only_the_named_channels(self):
    async with self.pipettes._temporary_y_drive_profile(acceleration_level=1, channels=[6, 7]):
      pass
    self.assertEqual(self.sent, ["P7AAyr1", "P8AAyr1", "P7AAyr3", "P8AAyr3"])


class TestDriveParametersAtSetup(unittest.IsolatedAsyncioTestCase):
  """Setup writes the driver's defaults into every channel, whatever an earlier session left."""

  async def test_every_channel_holds_the_defaults_after_setup(self):
    pipettes = await simulated_channels()
    for channel in range(pipettes.num_channels):
      self.assertEqual(await pipettes.request_y_speed(channel), 249.98)
      self.assertEqual(await pipettes.request_y_acceleration_level(channel), 3)
      self.assertEqual(await pipettes.request_z_speed(channel), 125.0)
      self.assertEqual(await pipettes.request_z_acceleration(channel), 804.6)

  async def test_setup_writes_four_per_channel(self):
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    sent: List[str] = []
    answer = driver.send_command

    async def recorded(module: str, command: str, **kwargs: Any):
      if command == "AA":
        sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
      return await answer(module=module, command=command, **kwargs)

    driver.send_command = recorded  # type: ignore[assignment]
    await driver.setup()
    written = [command for command in sent if command[0] == "P"]
    self.assertEqual(len(written), 32)
    self.assertEqual(written[:4], ["P1AAyv5399", "P1AAyr3", "P1AAzv11652", "P1AAzr075"])

  async def test_a_repeated_setup_puts_back_what_a_session_changed(self):
    pipettes = await simulated_channels()
    await pipettes._set_z_speed(2, 50.0)
    await pipettes._set_y_acceleration_level(2, 1)
    await pipettes._driver.setup()
    self.assertEqual(await pipettes.request_z_speed(2), 125.0)
    self.assertEqual(await pipettes.request_y_acceleration_level(2), 3)

  async def test_a_channel_move_writes_what_it_moved_with(self):
    pipettes = await simulated_channels()
    z = pipettes.configuration.z_range[1]
    await pipettes.move_stop_disc_to_z_position(0, z, speed=100.0, acceleration=300.0)
    self.assertEqual(await pipettes.request_z_speed(0), 100.0)
    self.assertEqual(await pipettes.request_z_acceleration(0), 300.4)


class TestRequireISWAPParked(unittest.IsolatedAsyncioTestCase):
  """The CoRe gripper commands refuse unless the iSWAP is parked; the check only reads."""

  async def asyncSetUp(self):
    self.pipettes = await simulated_channels()
    self.sent: List[str] = []
    answer = self.pipettes._driver.send_command

    async def recorded(module: str, command: str, **kwargs: Any):
      self.sent.append(module + command)
      return await answer(module=module, command=command, **kwargs)

    self.pipettes._driver.send_command = recorded  # type: ignore[assignment]

  async def test_parked_reads_only(self):
    await self.pipettes._require_iswap_parked()
    self.assertEqual(self.sent, ["R0RY", "R0RZ", "R0RW", "R0RT", "R0RG"])

  async def test_not_parked_refuses(self):
    iswap = self.pipettes.arm.iswap
    assert iswap is not None

    async def not_parked(*args: Any, **kwargs: Any) -> bool:
      return False

    iswap.request_parked = not_parked  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.pipettes._require_iswap_parked()

  async def test_no_iswap_sends_nothing(self):
    self.pipettes.arm.iswap = None
    await self.pipettes._require_iswap_parked()
    self.assertEqual(self.sent, [])


class TestSafeZAndStopDiscMoves(unittest.IsolatedAsyncioTestCase):
  """Safe Z is the firmware's own move under a Z profile; stop-disc moves go out together."""

  async def asyncSetUp(self):
    self.pipettes = await simulated_channels()
    self.sent: List[str] = []
    answer = self.pipettes._driver.send_command

    async def recorded(module: str, command: str, **kwargs: Any):
      wire = {k: v for k, v in kwargs.items() if len(k) == 2 and not isinstance(v, list)}
      if command in ("ZA", "AA"):
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.pipettes._driver.send_command = recorded  # type: ignore[assignment]

  async def test_safe_z_is_one_command(self):
    await self.pipettes.move_to_safe_z()
    self.assertEqual(self.sent, ["C0ZA"])

  async def test_safe_z_at_a_speed_holds_it_for_the_move(self):
    await self.pipettes.move_to_safe_z(speed=50.0)
    self.assertEqual(
      self.sent,
      [f"P{i}AAzv04661" for i in "12345678"] + ["C0ZA"] + [f"P{i}AAzv11652" for i in "12345678"],
    )

  async def test_a_failed_safe_z_still_puts_back_the_speed(self):
    async def refused() -> List[float]:
      raise RuntimeError("the probe")

    self.pipettes.probe_z_max = refused  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.pipettes.move_to_safe_z(speed=50.0)
    self.assertEqual(
      self.sent, [f"P{i}AAzv04661" for i in "12345678"] + [f"P{i}AAzv11652" for i in "12345678"]
    )

  async def test_stop_disc_moves_are_checked_before_any_is_sent(self):
    with self.assertRaises(ValueError):
      await self.pipettes.move_stop_disc_to_z_positions({0: 300.0, 3: 50.0})
    self.assertEqual(self.sent, [])

  async def test_a_failing_channel_does_not_stop_the_others(self):
    moved: List[int] = []
    move = self.pipettes.move_stop_disc_to_z_position

    async def one(channel: int, z: float, **kwargs: Any):
      if channel == 2:
        raise RuntimeError("channel 2")
      moved.append(channel)
      return await move(channel, z, **kwargs)

    self.pipettes.move_stop_disc_to_z_position = one  # type: ignore[method-assign, assignment]
    with self.assertRaises(RuntimeError):
      await self.pipettes.move_stop_disc_to_z_positions({ch: 300.0 for ch in range(8)})
    self.assertEqual(sorted(moved), [0, 1, 3, 4, 5, 6, 7])


class TestCLLDProbing(unittest.IsolatedAsyncioTestCase):
  """`C0 XL` and `Px YL` as legacy sends them, and what the probes make of the positions read."""

  async def asyncSetUp(self):
    self.pipettes = await simulated_channels()
    self.sent: List[str] = []

    async def recorded(
      module: str, command: str, fmt: Optional[Any] = None, read_timeout: float = 0, **kwargs: Any
    ):
      self.sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))

    self.pipettes._driver.send_command = recorded  # type: ignore[assignment]
    self._carry_tips(True)
    self.moves = unittest.mock.AsyncMock()
    self.ys = [400.0, 300.0, 200.0, 100.0, 90.0, 80.0, 70.0, 60.0][: self.pipettes.num_channels]

  def _carry_tips(self, carried: bool):
    self.pipettes.sense_tip_presence = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      return_value=[int(carried)] * self.pipettes.num_channels
    )

  def _answer_the_search_with(self, command: str, reply: str):
    """Answer `command` with the firmware error `reply` parses to; record everything."""
    recorded = self.pipettes._driver.send_command
    searched = command

    async def answering(module: str, command: str, **kwargs: Any):
      await recorded(module=module, command=command, **kwargs)
      if command == searched:
        check_fw_string_error(reply)

    self.pipettes._driver.send_command = answering  # type: ignore[assignment]

  def _stand_at_x(self, *xs: float):
    reads = unittest.mock.AsyncMock(side_effect=list(xs))
    self.pipettes.request_x_position = reads  # type: ignore[method-assign]
    self.pipettes.move_to_x_position = self.moves  # type: ignore[method-assign]

  def _stand_at_y(self, *ys: List[float]):
    reads = unittest.mock.AsyncMock(side_effect=list(ys))
    self.pipettes.request_y_positions = reads  # type: ignore[method-assign]
    self.pipettes.move_to_y_position = self.moves  # type: ignore[method-assign]

  async def test_x_firmware(self):
    await self.pipettes._unchecked_fw_probe_x_using_clld(134.0)
    self.assertEqual(self.sent, ["C0XLxs01340"])

  async def test_y_firmware(self):
    await self.pipettes._unchecked_fw_probe_y_using_clld(0, 2160, 10, 216, 4, 7)
    self.assertEqual(self.sent, ["P1YLya02160gt0010gl0000yv0216yr4yw7"])

  async def test_x_probe_searches_backs_away_and_corrects_for_the_tip(self):
    self._stand_at_x(300.0, 250.04)
    x = await self.pipettes.probe_x_using_clld(0, "left", search_end_position=200.0)
    self.assertEqual(self.sent, ["C0XLxs02000"])
    self.moves.assert_awaited_once_with(252.0)
    self.assertEqual(x, 249.4)

  async def test_x_probe_searches_to_the_end_of_its_reach_by_default(self):
    device = self.pipettes._driver.configuration
    assert device is not None
    high = device.instrument_size_slots * 22.5 + 125.0
    self._stand_at_x(300.0, 400.0)
    x = await self.pipettes.probe_x_using_clld(0, "right")
    self.assertEqual(self.sent, [f"C0XLxs{round(high * 10):05}"])
    self.moves.assert_awaited_once_with(398.0)
    self.assertEqual(x, 400.6)

  async def test_x_probe_refuses_an_end_behind_the_arm_or_out_of_reach(self):
    searches: List[Tuple[Literal["left", "right"], float]] = [("right", 200.0), ("left", 94.9)]
    for direction, end in searches:
      self._stand_at_x(300.0)
      with self.assertRaises(ValueError):
        await self.pipettes.probe_x_using_clld(0, direction, search_end_position=end)
    self.assertEqual(self.sent, [])

  async def test_y_probe_searches_to_the_neighbour_by_default(self):
    after = list(self.ys)
    after[1] = 250.0
    self._stand_at_y(self.ys, self.ys, after)
    front = 200.0 + self.pipettes._min_spacing_between(1, 2)
    y = await self.pipettes.probe_y_using_clld(1, "forward")
    end = self.pipettes.configuration.y_drive_mm_to_increments(front)
    self.assertEqual(self.sent, [f"P2YLya{end:05}gt0010gl0000yv0216yr4yw7"])
    self.moves.assert_awaited_once_with(1, 252.0)
    self.assertEqual(y, 249.4)

  async def test_y_probe_backs_away_no_further_than_the_neighbour_allows(self):
    back = 300.0 - self.pipettes._min_spacing_between(1, 2)
    front = 100.0 + self.pipettes._min_spacing_between(2, 3)
    after = list(self.ys)
    after[2] = front + 0.5
    self._stand_at_y(self.ys, self.ys, after)
    y = await self.pipettes.probe_y_using_clld(2, "backward")
    end = self.pipettes.configuration.y_drive_mm_to_increments(back)
    self.assertEqual(self.sent, [f"P3YLya{end:05}gt0010gl0000yv0216yr4yw7"])
    self.moves.assert_awaited_once_with(2, front)
    self.assertEqual(y, round(front + 0.5 + 0.6, 1))

  async def test_y_probe_refuses_a_start_or_end_past_the_neighbour(self):
    for kwargs in ({"search_start_position": 399.0}, {"search_end_position": 150.0}):
      self._stand_at_y(self.ys)
      with self.assertRaises(ValueError):
        await self.pipettes.probe_y_using_clld(1, "forward", **kwargs)  # type: ignore[arg-type]
    self.assertEqual(self.sent, [])

  async def test_y_probe_refuses_a_drive_setting_out_of_range(self):
    for kwargs in ({"acceleration_level": 5}, {"current_limit": 8}, {"detection_edge": 1024}):
      self._stand_at_y(self.ys, self.ys)
      with self.assertRaises(ValueError):
        await self.pipettes.probe_y_using_clld(1, "forward", **kwargs)  # type: ignore[arg-type]
    self.assertEqual(self.sent, [])

  async def test_x_probe_that_finds_nothing_backs_away_and_answers_none(self):
    self._answer_the_search_with("XL", "C0XLid0001er12/00")
    self._stand_at_x(300.0, 200.0)
    self.assertIsNone(await self.pipettes.probe_x_using_clld(0, "left", search_end_position=200.0))
    self.moves.assert_awaited_once_with(202.0)

  async def test_y_probe_that_finds_nothing_backs_away_and_answers_none(self):
    for trace in (70, 73):
      self.moves.reset_mock()
      self._answer_the_search_with("YL", f"P2YLid0001er{trace}")
      self._stand_at_y(self.ys, self.ys, self.ys)
      self.assertIsNone(await self.pipettes.probe_y_using_clld(1, "forward"))
      self.moves.assert_awaited_once_with(1, 302.0)

  async def test_any_other_error_is_raised_and_nothing_backs_away(self):
    self._answer_the_search_with("XL", "C0XLid0001er02/00")
    self._stand_at_x(300.0, 250.0)
    with self.assertRaises(STARFirmwareError):
      await self.pipettes.probe_x_using_clld(0, "left", search_end_position=200.0)
    # A trace 70 on another channel is not this search finding nothing.
    for reply in ("P2YLid0001er99", "P3YLid0001er70"):
      self._answer_the_search_with("YL", reply)
      self._stand_at_y(self.ys, self.ys, self.ys)
      with self.assertRaises(STARFirmwareError):
        await self.pipettes.probe_y_using_clld(1, "forward")
    self.moves.assert_not_awaited()

  async def test_a_bare_channel_probes_on_its_stop_disc_only_when_allowed(self):
    self._carry_tips(False)
    self._stand_at_x(300.0, 250.0)
    with self.assertRaises(RuntimeError):
      await self.pipettes.probe_x_using_clld(0, "left", search_end_position=200.0)
    self._stand_at_y(self.ys)
    with self.assertRaises(RuntimeError):
      await self.pipettes.probe_y_using_clld(1, "forward")
    self.assertEqual(self.sent, [])

    x = await self.pipettes.probe_x_using_clld(
      0, "left", search_end_position=200.0, allow_without_tip=True
    )
    self.assertEqual(x, 250.0 - 7.0 / 2)
    after = list(self.ys)
    after[1] = 250.0
    self._stand_at_y(self.ys, self.ys, after)
    y = await self.pipettes.probe_y_using_clld(1, "forward", allow_without_tip=True)
    self.assertEqual(y, 250.0 - 7.0 / 2)


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

  async def test_the_model_answers_for_each_channel_s_shaft_and_tip(self):
    """What the model holds, read without asking the device."""
    self.assertIs(self.pipettes.shaft(0), self.shaft)
    self.assertIsNone(self.pipettes.shaft(len(self.pipettes.resources)))
    self.assertIsNone(self.pipettes.get_mounted_tip(0))
    self.shaft.mount_tip(self.tip)
    self.assertIs(self.pipettes.get_mounted_tip(0), self.tip)
    self.assertIsNone(self.pipettes.get_mounted_tip(1))

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
    self.assertAlmostEqual(overhang, self.tip.get_size_z() - self.tip.fitting_depth, places=1)

  async def test_the_grip_tool_is_positioned_by_its_grip_line(self):
    """The grip line sits 2 mm above the bottom of the 32 mm body."""
    from pylabrobot.resources.hamilton import hamilton_core_gripper_tool

    self.shaft.mount_tip(hamilton_core_gripper_tool(name="grip"))
    self.assertAlmostEqual(await self.pipettes.request_tip_overhang(0), 22.0, places=1)
    await self.pipettes.move_tool_bottom_to_z_positions({0: 200.0})
    self.assertAlmostEqual(await self.pipettes.request_stop_disc_z_position(0), 222.0, places=1)
    self.assertAlmostEqual(await self.pipettes.request_tool_bottom_z_position(0), 200.0, places=1)

  async def test_the_tip_bottoms_answer_a_list_by_channel(self):
    from pylabrobot.resources.hamilton import hamilton_tip_300uL
    from pylabrobot.resources.n_channel_pipettes import TipMountingShaft

    for channel, resource in enumerate(self.pipettes.resources):
      shaft = next(child for child in resource.children if isinstance(child, TipMountingShaft))
      shaft.mount_tip(hamilton_tip_300uL(name=f"tip_{channel}"))
    bottoms = await self.pipettes.request_tool_bottom_z_positions()
    self.assertIsInstance(bottoms, list)
    self.assertEqual(len(bottoms), self.pipettes.num_channels)
    self.assertEqual(bottoms[5], await self.pipettes.request_tool_bottom_z_position(5))

  async def test_moving_the_tip_end_puts_the_stop_disc_an_overhang_higher(self):
    self.shaft.mount_tip(self.tip)
    low, high = self.pipettes.configuration.z_range
    z = (low + high) / 2 - 20
    await self.pipettes.move_tool_bottom_to_z_positions({0: z})
    stop_disc = await self.pipettes.request_stop_disc_z_position(0)
    self.assertAlmostEqual(stop_disc - z, self.tip.get_size_z() - self.tip.fitting_depth, places=1)


def travel_time(distance: float, speed: float, acceleration: float) -> float:
  """How long a trapezoidal move takes, in seconds: the simulator's timing, worked independently."""
  if distance * acceleration < speed * speed:
    return 2 * math.sqrt(distance / acceleration)
  return distance / speed + speed / acceleration


class TestSimulatedMotionTime(unittest.IsolatedAsyncioTestCase):
  """A simulated move owes the time its drives would take, and the next command waits it out.

  The clock is a mock, so what is checked is what would have been waited, not how long it took.
  """

  async def asyncSetUp(self):
    patcher = unittest.mock.patch("asyncio.sleep", new_callable=unittest.mock.AsyncMock)
    self.sleep = patcher.start()
    self.addCleanup(patcher.stop)

  async def timed_channels(self, simulate_motion_time: bool) -> Pipettes:
    """The channels of a simulated device keeping time at its full rate, with nothing owed."""
    driver = STARSimulationDriver(
      deck=STARDeck(),
      declared_configuration_json=RECORDING_STAR,
      simulate_motion_time=simulate_motion_time,
      motion_time_scale=1.0,
    )
    await driver.setup()
    assert driver.pipettes is not None
    await driver.pay_motion_time()
    self.sleep.reset_mock()
    return driver.pipettes

  async def test_a_z_move_is_waited_out_before_the_next_command(self):
    pipettes = await self.timed_channels(simulate_motion_time=True)
    c = pipettes.configuration
    z = await pipettes.request_stop_disc_z_position(0)
    await pipettes.move_stop_disc_to_z_position(0, z - 50.0)
    self.sleep.assert_not_awaited()

    await pipettes.request_stop_disc_z_position(0)
    self.sleep.assert_awaited_once()
    waited = self.sleep.await_args_list[0].args[0]
    expected = travel_time(50.0, c.z_drive_speed_default, c.z_drive_acceleration_default)
    self.assertAlmostEqual(waited, expected, places=3)

  async def test_channels_moving_together_take_as_long_as_the_farthest(self):
    pipettes = await self.timed_channels(simulate_motion_time=True)
    last = pipettes.num_channels - 1
    ys = await pipettes.request_y_positions()
    await pipettes.move_to_y_positions({0: ys[0] + 30.0, last: ys[last] - 10.0})
    await pipettes.request_y_positions()
    self.sleep.assert_awaited_once()
    self.assertAlmostEqual(
      self.sleep.await_args_list[0].args[0], 30.0 / Pipettes.default_y_speed, places=3
    )

  async def test_nothing_is_waited_when_the_device_keeps_no_time(self):
    pipettes = await self.timed_channels(simulate_motion_time=False)
    z = await pipettes.request_stop_disc_z_position(0)
    await pipettes.move_stop_disc_to_z_position(0, z - 50.0)
    await pipettes.request_stop_disc_z_position(0)
    self.sleep.assert_not_awaited()

  async def test_the_default_is_a_quarter_of_the_device_time(self):
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=RECORDING_STAR, simulate_motion_time=True
    )
    driver.owe_motion_time(2.0)
    await driver.pay_motion_time()
    self.sleep.assert_awaited_once_with(0.5)
