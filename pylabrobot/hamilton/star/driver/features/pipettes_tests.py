import unittest
import unittest.mock
from typing import Any, List, Optional, Tuple

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.errors import STARFirmwareError, check_fw_string_error
from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes, PipettesConfiguration
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.lib.liquid_handling.pipette_batch_scheduling import plan_batches
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
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
    self.assertEqual(self.sent, [])

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


class TestZTouchFirmware(unittest.IsolatedAsyncioTestCase):
  """`Px ZH` as it goes on the wire, byte for byte as legacy sends it."""

  async def asyncSetUp(self):
    self.pipettes = await simulated_channels()
    self.sent: List[str] = []

    async def recorded(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
      self.sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
      return {"rz": 20000}

    self.pipettes._driver.send_command = recorded  # type: ignore[assignment]

  async def test_legacys_defaults(self):
    rz = await self.pipettes._unchecked_fw_probe_z_using_ztouch(
      7, 31200, 9320, 932, 11652, 75, 1, 0
    )
    self.assertEqual(self.sent, ["P8ZHzb31200za09320zv11652zr075zu00932cg001cf000"])
    self.assertEqual(rz, 20000)

  async def test_a_window_a_speed_and_a_push_force(self):
    await self.pipettes._unchecked_fw_probe_z_using_ztouch(0, 28142, 13983, 466, 11652, 75, 1, 10)
    self.assertEqual(self.sent, ["P1ZHzb28142za13983zv11652zr075zu00466cg001cf010"])


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
    self.moves = unittest.mock.AsyncMock()

  async def test_x_firmware(self):
    await self.pipettes._unchecked_fw_probe_x_using_clld(134.0)
    self.assertEqual(self.sent, ["C0XLxs01340"])

  async def test_y_firmware(self):
    await self.pipettes._unchecked_fw_probe_y_using_clld(0, 2160, 10, 216, 4, 7)
    self.assertEqual(self.sent, ["P1YLya02160gt0010gl0000yv0216yr4yw7"])

  async def test_x_probe_searches_backs_away_and_corrects_for_the_tip(self):
    reads = unittest.mock.AsyncMock(side_effect=[300.0, 250.04])
    self.pipettes.request_x_position = reads  # type: ignore[method-assign]
    self.pipettes.move_to_x_position = self.moves  # type: ignore[method-assign]
    x = await self.pipettes.probe_x_using_clld(0, "left", search_end_position=200.0)
    self.assertEqual(self.sent, ["C0XLxs02000"])
    self.moves.assert_awaited_once_with(252.0)
    self.assertEqual(x, 249.4)

  async def test_x_probe_refuses_an_end_behind_the_arm(self):
    self.pipettes.request_x_position = unittest.mock.AsyncMock(return_value=300.0)  # type: ignore[method-assign]
    with self.assertRaises(ValueError):
      await self.pipettes.probe_x_using_clld(0, "right", search_end_position=200.0)
    self.assertEqual(self.sent, [])

  async def test_y_probe_searches_to_the_neighbour_by_default(self):
    ys = [400.0, 300.0, 200.0, 100.0, 90.0, 80.0, 70.0, 60.0][: self.pipettes.num_channels]
    after = list(ys)
    after[1] = 250.0
    self.pipettes.request_y_positions = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      side_effect=[ys, ys, after]
    )
    self.pipettes.move_to_y_position = self.moves  # type: ignore[method-assign]
    front = 200.0 + self.pipettes._min_spacing_between(1, 2)
    y = await self.pipettes.probe_y_using_clld(1, "forward")
    end = self.pipettes.configuration.y_drive_mm_to_increments(front)
    self.assertEqual(self.sent, [f"P2YLya{end:05}gt0010gl0000yv0216yr4yw7"])
    self.moves.assert_awaited_once_with(1, 252.0)
    self.assertEqual(y, 249.4)

  async def test_z_firmware(self):
    await self.pipettes._unchecked_fw_probe_z_using_clld(0, 9320, 31200, 932, 75, 10, 2, 1, 186)
    self.assertEqual(self.sent, ["P1ZLzh09320zc31200zl00932zr075gt0010gl0002zj1zi0186"])

  def _mount_a_tip(self, overhang: float):
    self.pipettes.sense_tip_presence = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      return_value=[1] * self.pipettes.num_channels
    )
    self.pipettes.request_tip_overhang = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      return_value=overhang
    )
    self.pipettes.move_to_safe_z = self.moves  # type: ignore[method-assign]

  async def test_z_probe_searches_from_the_top_on_the_stop_disc_and_reads_the_height(self):
    self._mount_a_tip(51.9)
    answer = self.pipettes._driver.send_command

    async def recorded(module: str, command: str, **kwargs: Any):
      await answer(module=module, command=command, **kwargs)
      return {"lh": [1234] * self.pipettes.num_channels}

    self.pipettes._driver.send_command = recorded  # type: ignore[assignment]
    z = await self.pipettes.probe_z_using_clld(1)
    end = self.pipettes.configuration.z_drive_mm_to_increments(99.98 + 51.9)
    self.assertEqual(self.sent, [f"P2ZLzh{end:05}zc31200zl00932zr075gt0010gl0002zj1zi0186", "C0RL"])
    self.assertEqual(z, 123.4)
    self.moves.assert_not_awaited()

  async def test_z_probe_goes_to_safe_z_on_a_firmware_error(self):
    self._mount_a_tip(51.9)

    async def failing(module: str, command: str, **kwargs: Any):
      raise STARFirmwareError(errors={}, raw_response="")

    self.pipettes._driver.send_command = failing  # type: ignore[assignment]
    with self.assertRaises(STARFirmwareError):
      await self.pipettes.probe_z_using_clld(0)
    self.moves.assert_awaited_once()

  def _answer_the_search_with(self, command: str, reply: str):
    """Answer `command` with the firmware error `reply` parses to; record everything."""
    recorded = self.pipettes._driver.send_command
    searched = command

    async def answering(module: str, command: str, **kwargs: Any):
      await recorded(module=module, command=command, **kwargs)
      if command == searched:
        check_fw_string_error(reply)
      return {"lh": [0] * self.pipettes.num_channels}

    self.pipettes._driver.send_command = answering  # type: ignore[assignment]

  async def test_x_probe_that_finds_nothing_backs_away_and_answers_none(self):
    self._answer_the_search_with("XL", "C0XLid0001er12/00")
    self.pipettes.request_x_position = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      side_effect=[300.0, 200.0]
    )
    self.pipettes.move_to_x_position = self.moves  # type: ignore[method-assign]
    self.assertIsNone(await self.pipettes.probe_x_using_clld(0, "left", search_end_position=200.0))
    self.moves.assert_awaited_once_with(202.0)

  async def test_y_probe_that_finds_nothing_backs_away_and_answers_none(self):
    self._answer_the_search_with("YL", "P2YLid0001er70")
    ys = [400.0, 300.0, 200.0, 100.0, 90.0, 80.0, 70.0, 60.0][: self.pipettes.num_channels]
    self.pipettes.request_y_positions = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      return_value=ys
    )
    self.pipettes.move_to_y_position = self.moves  # type: ignore[method-assign]
    self.assertIsNone(await self.pipettes.probe_y_using_clld(1, "forward"))
    self.moves.assert_awaited_once()

  async def test_z_probe_that_finds_nothing_goes_to_safe_z_and_answers_none(self):
    self._mount_a_tip(51.9)
    self._answer_the_search_with("ZL", "P2ZLid0001er70")
    self.assertIsNone(await self.pipettes.probe_z_using_clld(1))
    self.moves.assert_awaited_once()
    self.assertNotIn("C0RL", self.sent)

  async def test_z_probe_raises_any_other_channel_error(self):
    self._mount_a_tip(51.9)
    self._answer_the_search_with("ZL", "P2ZLid0001er99")
    with self.assertRaises(STARFirmwareError):
      await self.pipettes.probe_z_using_clld(1)
    self.moves.assert_awaited_once()

  async def test_z_probe_refuses_a_channel_without_a_tip(self):
    self.pipettes.sense_tip_presence = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      return_value=[0] * self.pipettes.num_channels
    )
    with self.assertRaises(RuntimeError):
      await self.pipettes.probe_z_using_clld(0)
    self.assertEqual(self.sent, [])

  async def test_y_probe_refuses_a_start_past_the_neighbour(self):
    ys = [400.0, 300.0, 200.0, 100.0, 90.0, 80.0, 70.0, 60.0][: self.pipettes.num_channels]
    self.pipettes.request_y_positions = unittest.mock.AsyncMock(return_value=ys)  # type: ignore[method-assign]
    with self.assertRaises(ValueError):
      await self.pipettes.probe_y_using_clld(1, "forward", search_start_position=399.0)
    self.assertEqual(self.sent, [])


class TestBatchPlanning(unittest.IsolatedAsyncioTestCase):
  """A v1 device plans with `pylabrobot.lib.liquid_handling`, from its own minimum channel spacing."""

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
    self.assertAlmostEqual(overhang, self.tip.get_size_z() - self.tip.fitting_depth, places=1)

  async def test_the_grip_tool_reaches_to_its_grip_line_as_the_firmware_counts_it(self):
    """The firmware counts the grip tool to its grip line, 30 mm, not to its 32 mm bottom."""
    from pylabrobot.resources.hamilton import hamilton_core_gripper_tool

    self.shaft.mount_tip(hamilton_core_gripper_tool(name="grip"))
    self.assertAlmostEqual(await self.pipettes.request_tip_overhang(0), 30.0 - 8.0, places=1)
    await self.pipettes.move_tool_bottom_to_z_positions({0: 200.0})
    self.assertAlmostEqual(await self.pipettes.request_stop_disc_z_position(0), 222.0, places=1)
    self.assertAlmostEqual(await self.pipettes.request_tool_bottom_z_position(0), 200.0, places=1)

  async def test_moving_the_tip_end_puts_the_stop_disc_an_overhang_higher(self):
    self.shaft.mount_tip(self.tip)
    low, high = self.pipettes.configuration.z_range
    z = (low + high) / 2 - 20
    await self.pipettes.move_tool_bottom_to_z_positions({0: z})
    stop_disc = await self.pipettes.request_stop_disc_z_position(0)
    self.assertAlmostEqual(stop_disc - z, self.tip.get_size_z() - self.tip.fitting_depth, places=1)


async def channels_over_a_rack() -> Tuple[Pipettes, Any, List[str]]:
  """Simulated channels, a 300 uL rack on track 16, and every tip command sent from here on.

  Returns:
    The feature, the rack, and the `C0 TT`, `TP`, `TR`, `EP` and `ER` commands as sent, without
    their ids.
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
    if written[:4] in ("C0TT", "C0TP", "C0TR", "C0EP", "C0ER"):
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
    self.assertAlmostEqual(stop_disc - bottom, tip.get_size_z() - tip.fitting_depth, places=1)

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
    shaft = pipettes.shaft(1)
    assert shaft is not None
    shaft.mount_tip(tip)
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
    shaft = pipettes.shaft(0)
    assert shaft is not None
    shaft.mount_tip(hamilton_tip_300uL(name="loose"))
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

  async def test_a_model_that_cannot_be_updated_does_not_hide_the_devices_error(self):
    """What the device said is the error worth having; a stale model is only logged."""
    from unittest.mock import AsyncMock, patch

    pipettes, rack, _ = await channels_over_a_rack()
    spot = rack.get_item("A1")
    with (
      patch.object(
        pipettes, "_unchecked_fw_pick_up_tips", AsyncMock(side_effect=RuntimeError("the device"))
      ),
      patch.object(pipettes, "sense_tip_presence", AsyncMock(return_value=[1] + [0] * 7)),
      patch.object(
        pipettes.shaft(0), "mount_tip", unittest.mock.Mock(side_effect=RuntimeError("the model"))
      ),
    ):
      with self.assertRaisesRegex(RuntimeError, "the device"):
        await pipettes.pick_up_tips([spot])

  async def test_a_channel_with_nothing_modelling_it_is_refused(self):
    """A deck given after setup leaves the channels unmodelled: the tip would belong to nothing."""
    pipettes, rack, sent = await channels_over_a_rack()
    pipettes.resources = []
    with self.assertRaises(RuntimeError):
      await pipettes.pick_up_tips([rack.get_item("A1")])
    self.assertIsNotNone(rack.get_item("A1").tip)
    self.assertEqual(sent, [])

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

  async def test_a_failed_pickup_lifts_the_channels_out_of_the_rack(self):
    """Whatever state a failure leaves them in, the channels come up to the traverse height."""
    from unittest.mock import AsyncMock, patch

    pipettes, rack, _ = await channels_over_a_rack()
    with patch.object(pipettes, "_unchecked_fw_pick_up_tips", AsyncMock(side_effect=RuntimeError)):
      with self.assertRaises(RuntimeError):
        await pipettes.pick_up_tips([rack.get_item("A1")])
    self.assertAlmostEqual(
      await pipettes.request_stop_disc_z_position(0),
      pipettes.default_minimum_traverse_height,
      places=1,
    )

  async def test_a_pickup_that_faults_on_one_channel_keeps_the_others_tips(self):
    """Not every pick-up uses every channel: the error names the ones that faulted, by channel.

    The channels cannot be asked here, so what the error says is what the model takes.
    """
    from unittest.mock import AsyncMock, patch

    from pylabrobot.hamilton.star.driver.errors import HardwareError, STARFirmwareError

    pipettes, rack, _ = await channels_over_a_rack()
    spots = [rack.get_item("A1"), rack.get_item("C1")]
    tips = [spot.tip for spot in spots]
    failure = STARFirmwareError(
      errors={
        "Pipetting channel 3": HardwareError(
          message="no tip picked up", trace_information=75, raw_response="", raw_module="P3"
        )
      },
      raw_response="C0TPid0001er99/00 P3/75",
    )
    with (
      patch.object(pipettes, "_unchecked_fw_pick_up_tips", AsyncMock(side_effect=failure)),
      patch.object(pipettes, "sense_tip_presence", AsyncMock(side_effect=RuntimeError)),
    ):
      with self.assertRaises(RuntimeError):
        await pipettes.pick_up_tips(spots, use_channels=[0, 2])
    self.assertIs(pipettes.get_mounted_tip(0), tips[0])
    self.assertIsNone(pipettes.get_mounted_tip(2))
    self.assertIs(tips[1].parent, spots[1])

  async def test_a_cancelled_pickup_moves_only_the_tips_the_channels_sense(self):
    """A cancellation is not an answer: what the channels carry is, and here they carry nothing."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    pipettes, rack, _ = await channels_over_a_rack()
    spots = [rack.get_item("A1"), rack.get_item("B1")]
    tips = [spot.tip for spot in spots]
    with patch.object(
      pipettes, "_unchecked_fw_pick_up_tips", AsyncMock(side_effect=asyncio.CancelledError)
    ):
      with self.assertRaises(asyncio.CancelledError):
        await pipettes.pick_up_tips(spots)
    self.assertIsNone(pipettes.get_mounted_tip(0))
    self.assertIs(tips[0].parent, spots[0])
    self.assertIs(tips[1].parent, spots[1])

  async def test_a_cancelled_drop_leaves_the_tips_the_channels_still_carry(self):
    import asyncio
    from unittest.mock import AsyncMock, patch

    pipettes, rack, _ = await channels_over_a_rack()
    spot = rack.get_item("A1")
    tip = spot.tip
    await pipettes.pick_up_tips([spot])
    with patch.object(
      pipettes, "_unchecked_fw_drop_tips", AsyncMock(side_effect=asyncio.CancelledError)
    ):
      with self.assertRaises(asyncio.CancelledError):
        await pipettes.drop_tips([spot])
    self.assertIs(pipettes.get_mounted_tip(0), tip)
    self.assertIs(tip.parent, pipettes.shaft(0))
    self.assertIsNone(spot.tip)

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
  """The commands for Hamilton's tip racks are what Hamilton's own software sends.

  Each rack on the holders Hamilton's software picked up from, with the channels and with the
  96-head, as recorded, but for `td`:
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
      hamilton_mfx_resourceholder_ntr,
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
          module = hamilton_mfx_resourceholder_ntr("ntr4_module")
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
          head96 = pipettes._driver.head96
          assert head96 is not None
          await head96.pick_up_tips(rack)
          await head96.drop_tips(rack)

          tt = sent[0][4:8]
          xp = " ".join([xs] * 8)
          yp = " ".join(f"{a1_y[size] - 90 * row:04}" for row in range(8))
          self.assertEqual(
            sent,
            [
              f"C0TT{tt}{definition}",
              f"C0TPxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{tt}tp{tp}tz1840th2450td0",
              f"C0TRxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{drop}th2450te2450ti1",
              f"C0EPxs{xs}xd0yh{a1_y[size]}{tt}wu0za1840zh2450ze2450",
              f"C0ERxs{xs}xd0yh{a1_y[size]}za1840zh2450ze2450",
            ],
          )

  async def test_framed_tip_racks_on_tip_carrier_ground_truth(self):
    from pylabrobot.resources.hamilton import (
      TIP_CAR_480BC_A00,
      hamilton_96_tiprack_10uL,
      hamilton_96_tiprack_50uL,
      hamilton_96_tiprack_300uL,
      hamilton_96_tiprack_300uL_filter_slim,
      hamilton_96_tiprack_1000uL_filter,
    )

    # rack, carrier x, site, tip definition (without its index), pick-up tp, drop tp/tz (stop disc,
    # ti1)
    tips = [
      (hamilton_96_tiprack_10uL, 437.5, 0, "tf0tl0219tv00150tg1tu0", "2224", "tp2224tz2144"),
      (hamilton_96_tiprack_50uL, 437.5, 1, "tf0tl0424tv00650tg2tu0", "2244", "tp2244tz2164"),
      (hamilton_96_tiprack_300uL, 572.5, 0, "tf0tl0519tv04000tg2tu0", "2244", "tp2244tz2164"),
      (
        hamilton_96_tiprack_1000uL_filter,
        572.5,
        1,
        "tf1tl0871tv10650tg3tu0",
        "2264",
        "tp2264tz2184",
      ),
      (
        hamilton_96_tiprack_300uL_filter_slim,
        572.5,
        2,
        "tf1tl0870tv03450tg3tu0",
        "2264",
        "tp2264tz2184",
      ),
    ]

    for rack_fn, carrier_x, site, definition, tp, drop in tips:
      with self.subTest(tip=rack_fn.__name__):
        pipettes, _, sent = await channels_over_a_rack()
        deck = pipettes._driver.deck
        assert deck is not None
        deck.unassign_child_resource(deck.get_resource("tip_carrier"))  # where this carrier goes
        carrier = TIP_CAR_480BC_A00("ground_truth_carrier")
        deck.assign_child_resource(carrier, location=Coordinate(carrier_x, 63, 100))
        carrier[site] = rack = rack_fn(rack_fn.__name__)
        sent.clear()

        spots = rack["A1:H1"]
        await pipettes.pick_up_tips(spots)
        await pipettes.drop_tips(spots)
        head96 = pipettes._driver.head96
        assert head96 is not None
        await head96.pick_up_tips(rack)
        await head96.drop_tips(rack)

        tt = sent[0][4:8]
        # A1 17.9 mm right of the carrier, 145.8 mm back on site 0, sites 96 mm apart (0.1 mm)
        xs, a1_y = f"{round(carrier_x * 10) + 179:05}", 1458 + 960 * site
        xp = " ".join([xs] * 8)
        yp = " ".join(f"{a1_y - 90 * row:04}" for row in range(8))
        self.assertEqual(
          sent,
          [
            f"C0TT{tt}{definition}",
            f"C0TPxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{tt}tp{tp}tz2164th2450td0",
            f"C0TRxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{drop}th2450te2450ti1",
            f"C0EPxs{xs}xd0yh{a1_y}{tt}wu0za2164zh2450ze2450",
            f"C0ERxs{xs}xd0yh{a1_y}za2164zh2450ze2450",
          ],
        )

  async def test_framed_tip_racks_on_mfx_tip_module_ground_truth(self):
    from pylabrobot.resources.hamilton import (
      hamilton_96_tiprack_10uL,
      hamilton_96_tiprack_50uL,
      hamilton_96_tiprack_300uL,
      hamilton_96_tiprack_300uL_filter_slim,
      hamilton_96_tiprack_1000uL_filter,
      hamilton_mfx_carrier_L5_base,
      hamilton_mfx_tiprackholder_standard,
    )

    # rack, MFX slot, tip definition (without its index), pick-up tp, drop tp/tz (stop disc, ti1)
    tips = [
      (hamilton_96_tiprack_10uL, 0, "tf0tl0219tv00150tg1tu0", "2222", "tp2222tz2142"),
      (hamilton_96_tiprack_50uL, 1, "tf0tl0424tv00650tg2tu0", "2242", "tp2242tz2162"),
      (hamilton_96_tiprack_300uL, 4, "tf0tl0519tv04000tg2tu0", "2242", "tp2242tz2162"),
      (hamilton_96_tiprack_1000uL_filter, 0, "tf1tl0871tv10650tg3tu0", "2262", "tp2262tz2182"),
      (hamilton_96_tiprack_300uL_filter_slim, 1, "tf1tl0870tv03450tg3tu0", "2262", "tp2262tz2182"),
    ]

    for rack_fn, slot, definition, tp, drop in tips:
      with self.subTest(tip=rack_fn.__name__):
        pipettes, _, sent = await channels_over_a_rack()
        deck = pipettes._driver.deck
        assert deck is not None
        module = hamilton_mfx_tiprackholder_standard("tip_module")
        deck.assign_child_resource(
          hamilton_mfx_carrier_L5_base("mfx_carrier", modules={slot: module}),
          location=Coordinate(752.5, 63, 100),
        )
        rack = rack_fn(rack_fn.__name__)
        module.assign_child_resource(rack)
        sent.clear()

        spots = rack["A1:H1"]
        await pipettes.pick_up_tips(spots)
        await pipettes.drop_tips(spots)
        head96 = pipettes._driver.head96
        assert head96 is not None
        await head96.pick_up_tips(rack)
        await head96.drop_tips(rack)

        tt = sent[0][4:8]
        # A1 146.0 mm back on slot 0, slots 96 mm apart (0.1 mm)
        xs, a1_y = "07705", 1460 + 960 * slot
        xp = " ".join([xs] * 8)
        yp = " ".join(f"{a1_y - 90 * row:04}" for row in range(8))
        self.assertEqual(
          sent,
          [
            f"C0TT{tt}{definition}",
            f"C0TPxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{tt}tp{tp}tz2162th2450td0",
            f"C0TRxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{drop}th2450te2450ti1",
            f"C0EPxs{xs}xd0yh{a1_y}{tt}wu0za2162zh2450ze2450",
            f"C0ERxs{xs}xd0yh{a1_y}za2162zh2450ze2450",
          ],
        )
