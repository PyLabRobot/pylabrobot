"""LED bar tests: the bytes sent, against replies recorded from a Mantis v4."""

import asyncio
import json
import os
import tempfile
import unittest
from typing import Dict, List, Tuple, Union
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features import led_bar
from pylabrobot.formulatrix.mantis.driver.features.led_bar import (
  DISPENSE_DONE_BLINK,
  LED_COLOR_CODES,
  SETUP_DONE_HOLD,
  LedBar,
  _compute_rgb565,
)
from pylabrobot.formulatrix.mantis.driver.features.plate_clamp_tests import (
  HALT,
  LED_BAR_COMMANDS,
  STATES_AFTER_LOCK_AT_LEVEL_2,
  STATES_AFTER_RELEASE_WHILE_LOCKED,
  STATES_AFTER_RELEASE_WHILE_RELEASED,
  answer_states_in_turn,
)
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestCase, FakeLink, make_answers

OFF, BLUE, GREEN, WHITE, RED, MAGENTA = 0x0000, 0x001F, 0x07E0, 0xFFFF, 0xF800, 0xF81F
# "#00FFFF", "#8000FF" and "#FF4000"; a dim white; "#FF0080".
CYAN, VIOLET, ORANGE, DIM_WHITE, PINK = 0x07FF, 0x801F, 0xFA00, 0x4208, 0xF810
# The colour writes the recorded v4 answered 1, shown: all 20 LEDs one colour, or 1 to 20 LEDs
# green and the rest off.
RECORDED_COLOR_WRITES: List[List[int]] = (
  [
    [color] * 20
    for color in (OFF, BLUE, GREEN, WHITE, RED, MAGENTA, CYAN, VIOLET, ORANGE, DIM_WHITE)
  ]
  + [[PINK] * 20]
  + [[GREEN] * lit + [OFF] * (20 - lit) for lit in range(1, 20)]
)
SHOWN = "0100"
# The animation stopped, and the idle animation (frames 0 to 86, repeating) started.
STOP_ANIMATION = (12, 16, bytes.fromhex("0000"))
START_IDLE_ANIMATION = (12, 17, bytes.fromhex("0000000056000100"))
STATE_READ = (12, 33, b"")


def color_write(*, colors: List[int]) -> Tuple[int, int, bytes]:
  """The colour write for ``colors``: strip 0, the count, then each colour, little-endian."""
  payload = "0000" + len(colors).to_bytes(2, "little").hex()
  payload += "".join(color.to_bytes(2, "little").hex() for color in colors)
  return (12, 13, bytes.fromhex(payload))


def make_led_bar_answers() -> Dict[Tuple[int, int, bytes], List[bytes]]:
  """The recorded replies, with every recorded colour write answered shown."""
  answers = make_answers()
  for colors in RECORDED_COLOR_WRITES:
    answers[color_write(colors=colors)] = [bytes.fromhex(SHOWN)]
  return answers


def led_bar_frames(*, written: List[bytes]) -> List[Tuple[int, int, bytes]]:
  """The LED bar's frames among ``written``."""
  frames = [parse_frame(frame=frame)[1:] for frame in written]
  return [frame for frame in frames if frame[:2] in LED_BAR_COMMANDS]


def bar_states(*, written: List[bytes]) -> List[Union[int, str, List[int]]]:
  """What the bar was told, in order: one colour, a colour per LED, or "idle"."""
  states: List[Union[int, str, List[int]]] = []
  for frame in led_bar_frames(written=written):
    if frame == START_IDLE_ANIMATION:
      states.append("idle")
    elif frame[1] == 13:
      payload = frame[2]
      colors = [int.from_bytes(payload[i : i + 2], "little") for i in range(4, len(payload), 2)]
      states.append(colors[0] if len(set(colors)) == 1 else colors)
  return states


class LedBarTestCase(DriverTestCase):
  async def set_up_bar(self, **kwargs) -> MantisDriver:
    self.link = FakeLink(answers=make_led_bar_answers())
    driver = await self.set_up_driver(link=self.link, **kwargs)
    self.sent_before = len(self.link.written)
    return driver

  def sent(self) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in self.link.written[self.sent_before :]]

  def states(self) -> List[Union[int, str, List[int]]]:
    return bar_states(written=self.link.written[self.sent_before :])


class ComputeRgb565Tests(unittest.TestCase):
  def test_web_hex_gives_each_named_colour(self):
    for web, name in (
      ("#000000", "off"),
      ("#0000FF", "blue"),
      ("#00FF00", "green"),
      ("#FFFF00", "yellow"),
      ("#FFFFFF", "white"),
      ("#FF0000", "red"),
      ("#FF00FF", "magenta"),
    ):
      with self.subTest(web=web):
        self.assertEqual(_compute_rgb565(color=web), LED_COLOR_CODES[name])

  def test_other_forms(self):
    self.assertEqual(_compute_rgb565(color="FF00FF"), MAGENTA)
    self.assertEqual(_compute_rgb565(color=0x001F), BLUE)
    self.assertEqual(_compute_rgb565(color="#FF7A00"), 0xFBC0)
    self.assertEqual(_compute_rgb565(color="#404040"), DIM_WHITE)

  def test_refuses_what_is_no_colour(self):
    for color in ("purple", "#ZZZZZZ", "#FF00F", 0x10000, -1):
      with self.subTest(color=color):
        with self.assertRaises(ValueError):
          _compute_rgb565(color=color)


class LedBarTests(LedBarTestCase):
  async def asyncSetUp(self):
    driver = await self.set_up_bar()
    assert driver.led_bar is not None
    self.bar = driver.led_bar

  async def test_set_color_stops_the_animation_then_writes_every_led(self):
    await self.bar.set_color(color="magenta")
    self.assertEqual(self.sent(), [STOP_ANIMATION, color_write(colors=[MAGENTA] * 20)])

  async def test_set_color_takes_a_web_hex_and_an_rgb565_value(self):
    for color in ("#FF00FF", "FF00FF", MAGENTA):
      with self.subTest(color=color):
        self.sent_before = len(self.link.written)
        await self.bar.set_color(color=color)
        self.assertEqual(self.sent(), [STOP_ANIMATION, color_write(colors=[MAGENTA] * 20)])

  async def test_set_color_one_per_led(self):
    await self.bar.set_color(color=["green"] * 10 + ["#000000"] * 9 + [OFF])
    self.assertEqual(self.sent(), [STOP_ANIMATION, color_write(colors=[GREEN] * 10 + [OFF] * 10)])

  async def test_set_color_refuses_and_sends_nothing(self):
    for color in ("purple", 0x10000, ["green"] * 19, ["green"] * 21):
      with self.subTest(color=color):
        with self.assertRaises(ValueError):
          await self.bar.set_color(color=color)
    self.assertEqual(self.sent(), [])

  async def test_turn_off(self):
    await self.bar.turn_off()
    self.assertEqual(self.sent(), [STOP_ANIMATION, color_write(colors=[OFF] * 20)])

  async def test_start_and_stop_animation(self):
    await self.bar.start_animation()
    await self.bar.stop_animation()
    self.assertEqual(self.sent(), [START_IDLE_ANIMATION, STOP_ANIMATION])

  async def test_start_animation_refuses_an_unknown_effect(self):
    with self.assertRaises(ValueError):
      await self.bar.start_animation(effect="strobe")
    self.assertEqual(self.sent(), [])

  async def test_fatal_error_and_unconnected_animations_sent(self):
    # Never answered on a device: only the frames sent are pinned.
    for effect in ("fatal_error", "unconnected"):
      with self.assertRaises(MantisTimeoutError):
        await self.bar.start_animation(effect=effect)
    self.assertEqual(
      self.sent(),
      [
        (12, 17, bytes.fromhex("0000200322030100")),
        (12, 17, bytes.fromhex("0000b603c8030100")),
      ],
    )


class LedBarDiscoveryTests(LedBarTestCase):
  async def test_built_only_on_a_v4_whose_board_12_answers(self):
    for absent_boards, built in (((), True), ((11,), False), ((12,), False)):
      with self.subTest(absent_boards=absent_boards):
        driver = await self.set_up_driver(
          link=FakeLink(answers=make_answers(absent_boards=absent_boards))
        )
        self.assertEqual(driver.led_bar is not None, built)

  async def test_the_led_count_comes_from_the_unit_block(self):
    driver = await self.set_up_bar(declared_configuration_json=RECORDING_MANTIS_V4)
    assert driver.led_bar is not None
    self.assertEqual(driver.led_bar.number_of_leds, 20)

  async def test_a_malformed_led_bar_block_refuses_setup(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    for block in ({"number_of_leds": "twenty"}, {"number_of_leds": 0}, {}):
      with self.subTest(block=block), tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "declared.json")
        with open(path, "w", encoding="utf-8") as f:
          json.dump({**recording, "led_bar": block}, f)
        driver = self.make_driver(
          link=FakeLink(answers=make_led_bar_answers()), declared_configuration_json=path
        )
        with self.assertRaisesRegex(MantisConfigurationError, "malformed led_bar block"):
          await driver.setup()

  async def test_a_v3_setup_and_stop_send_no_led_bar_frame(self):
    link = FakeLink(answers=make_answers(absent_boards=(11,)))
    driver = await self.set_up_driver(link=link)
    await driver.stop()
    self.assertEqual(led_bar_frames(written=link.written), [])


class SetupAndStopTests(LedBarTestCase):
  async def test_setup_shows_blue_then_green_held_then_idle(self):
    link = FakeLink(answers=make_led_bar_answers())
    driver = await self.set_up_driver(link=link)
    self.assertEqual(bar_states(written=link.written), [BLUE, GREEN, "idle"])
    self.assertEqual(
      led_bar_frames(written=link.written),
      [
        STOP_ANIMATION,
        color_write(colors=[BLUE] * 20),
        STOP_ANIMATION,
        color_write(colors=[GREEN] * 20),
        START_IDLE_ANIMATION,
      ],
    )
    self.led_bar_sleep.assert_awaited_once_with(driver.led_bar, seconds=SETUP_DONE_HOLD)

  async def test_setup_without_show_actions_plays_the_idle_animation_only(self):
    link = FakeLink(answers=make_led_bar_answers())
    await self.set_up_driver(link=link, show_actions=False)
    self.assertEqual(led_bar_frames(written=link.written), [START_IDLE_ANIMATION])
    self.led_bar_sleep.assert_not_awaited()

  async def test_stop_halts_then_releases_the_clamp_then_turns_off(self):
    driver = await self.set_up_bar()
    await driver.stop()
    self.assertEqual(
      self.sent(), [*HALT, STATE_READ, STOP_ANIMATION, color_write(colors=[OFF] * 20)]
    )

  async def test_stop_without_show_actions_turns_off_only(self):
    driver = await self.set_up_bar(show_actions=False)
    await driver.stop()
    self.assertEqual(
      led_bar_frames(written=self.link.written[self.sent_before :]),
      [STOP_ANIMATION, color_write(colors=[OFF] * 20)],
    )

  async def test_a_turn_off_that_fails_still_lets_the_next_setup_show(self):
    driver = await self.set_up_bar()
    del self.link.answers[color_write(colors=[OFF] * 20)]
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.master", level="WARNING"):
      await driver.stop()
    self.sent_before = len(self.link.written)
    await driver.setup(skip_arm=True)
    self.assertEqual(self.states(), [BLUE, GREEN, "idle"])

  async def test_clamp_lock_and_unlock_show_nothing(self):
    driver = await self.set_up_bar(declared_configuration_json=RECORDING_MANTIS_V4)
    assert driver.plate_clamp is not None
    driver.default_read_timeout = 5.0
    answer_states_in_turn(
      link=self.link,
      turns=[
        (31, STATES_AFTER_RELEASE_WHILE_RELEASED),
        (30, STATES_AFTER_LOCK_AT_LEVEL_2),
        (31, STATES_AFTER_RELEASE_WHILE_LOCKED),
      ],
    )
    await driver.plate_clamp.lock(level=2)
    await driver.plate_clamp.unlock()
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])


class ActionTests(LedBarTestCase):
  async def asyncSetUp(self):
    self.driver = await self.set_up_bar()
    assert self.driver.led_bar is not None
    self.bar = self.driver.led_bar

  async def test_a_nested_action_restores_the_enclosing_colour_then_idle(self):
    async with self.driver._show_action(action="setup"):
      async with self.driver._show_action(action="setup_done"):
        async with self.driver._show_action(action="error"):
          pass
    self.assertEqual(self.states(), [BLUE, GREEN, RED, GREEN, BLUE, "idle"])

  async def test_nothing_inside_setup_changes_the_bar(self):
    async with self.driver._show_action(action="setup", show_inner_actions=False):
      async with self.driver._show_action(action="error"):
        pass
    self.assertEqual(self.states(), [BLUE, "idle"])

  async def test_an_action_only_when_idle_shows_only_when_nothing_else_runs(self):
    async with self.driver._show_action(action="setup"):
      async with self.driver._show_action(action="setup_done", only_when_idle=True):
        pass
    async with self.driver._show_action(action="setup_done", only_when_idle=True):
      pass
    self.assertEqual(self.states(), [BLUE, "idle", GREEN, "idle"])

  async def test_a_failed_action_leaves_red_until_the_next_action(self):
    with self.assertRaises(ValueError):
      async with self.driver._show_action(action="setup"):
        async with self.driver._show_action(action="setup_done"):
          raise ValueError("no flow")
    self.assertEqual(self.states(), [BLUE, GREEN, RED])
    async with self.driver._show_action(action="setup"):
      pass
    self.assertEqual(self.states()[3:], [BLUE, "idle"])

  async def test_a_cancelled_action_writes_nothing_more(self):
    with self.assertRaises(asyncio.CancelledError):
      async with self.driver._show_action(action="setup"):
        raise asyncio.CancelledError()
    self.assertEqual(self.states(), [BLUE])
    self.assertEqual(self.bar._running_actions, [])

  async def test_a_led_bar_fault_neither_fails_nor_masks_the_action(self):
    del self.link.answers[STOP_ANIMATION]
    del self.link.answers[START_IDLE_ANIMATION]
    with self.assertLogs(led_bar.__name__, level="WARNING"):
      async with self.driver._show_action(action="setup"):
        pass
      with self.assertRaisesRegex(ValueError, "the action's own"):
        async with self.driver._show_action(action="setup"):
          raise ValueError("the action's own error")

  async def test_show_actions_off_sends_nothing(self):
    self.bar.show_actions = False
    async with self.driver._show_action(action="setup"):
      pass
    self.assertEqual(self.sent(), [])

  async def test_each_action_shows_its_colour_attribute_when_it_is_shown(self):
    with self.assertRaises(ValueError):
      async with self.driver._show_action(action="setup"):
        self.bar.setup_color = "magenta"
        self.bar.error_color = "#FF4000"
        async with self.driver._show_action(action="setup_done"):
          pass
        raise ValueError("no flow")
    self.assertEqual(self.states(), [BLUE, GREEN, MAGENTA, ORANGE])

  async def test_a_colour_attribute_refuses_what_is_no_colour_and_keeps_its_own(self):
    for color in ("purple", "#ZZZZZZ", "#FF00F", 0x10000, -1):
      with self.subTest(color=color):
        with self.assertRaises(ValueError):
          self.bar.setup_color = color
        with self.assertRaises(ValueError):
          self.bar.setup_done_color = color
        with self.assertRaises(ValueError):
          self.bar.error_color = color
        with self.assertRaises(ValueError):
          self.bar.dispense_progress_color = color
    self.assertEqual(
      [
        self.bar.setup_color,
        self.bar.setup_done_color,
        self.bar.error_color,
        self.bar.dispense_progress_color,
      ],
      ["blue", "green", "red", "green"],
    )
    self.assertEqual(self.sent(), [])

  async def test_each_bar_has_its_own_colours(self):
    other = LedBar(driver=self.driver, number_of_leds=20, show_actions=True)
    self.bar.setup_color = "FF00FF"
    self.bar.dispense_progress_color = MAGENTA
    self.assertEqual([self.bar.setup_color, self.bar.dispense_progress_color], ["FF00FF", MAGENTA])
    self.assertEqual([other.setup_color, other.dispense_progress_color], ["blue", "green"])

  async def test_show_progress_lights_a_rounded_share_from_the_left(self):
    for done, total in ((1, 3), (1, 2), (2, 3), (3, 3), (0, 3)):
      await self.bar.show_progress(done=done, total=total)
    self.assertEqual(
      self.states(),
      [
        [GREEN] * 7 + [OFF] * 13,
        [GREEN] * 10 + [OFF] * 10,
        [GREEN] * 13 + [OFF] * 7,
        GREEN,
        OFF,
      ],
    )
    self.assertEqual(
      self.sent()[:2], [STOP_ANIMATION, color_write(colors=[GREEN] * 7 + [OFF] * 13)]
    )

  async def test_show_progress_lights_one_led_once_progress_starts(self):
    await self.bar.show_progress(done=1, total=400)
    self.assertEqual(self.states(), [[GREEN] + [OFF] * 19])

  async def test_show_progress_sends_nothing_for_the_leds_already_lit(self):
    await self.bar.show_progress(done=1, total=3)
    await self.bar.show_progress(done=2, total=6)
    self.assertEqual(len(self.sent()), 2)
    await self.bar.set_color(color="blue")
    await self.bar.show_progress(done=2, total=6)
    self.assertEqual(self.states(), [[GREEN] * 7 + [OFF] * 13, BLUE, [GREEN] * 7 + [OFF] * 13])

  async def test_show_progress_refuses_and_sends_nothing(self):
    for done, total in ((0, 0), (-1, 3), (4, 3)):
      with self.subTest(done=done, total=total):
        with self.assertRaises(ValueError):
          await self.bar.show_progress(done=done, total=total)
    self.assertEqual(self.sent(), [])

  async def test_a_fill_lights_a_share_of_the_leds(self):
    await self.bar._show_fill(done=1, total=3)
    self.assertEqual(self.states(), [[GREEN] * 7 + [OFF] * 13])

  async def test_a_fill_fault_only_warns(self):
    del self.link.answers[STOP_ANIMATION]
    with self.assertLogs(led_bar.__name__, level="WARNING"):
      await self.bar._show_fill(done=1, total=3)

  async def test_the_fill_follows_the_played_wells_across_the_id_wrap(self):
    with mock.patch.object(led_bar, "FILL_INTERVAL", 0.01):
      follow = asyncio.create_task(self.bar._follow_fill(well_ends=[32766, 2], total=2))
      for progress in (32765, 32767, 3):
        self.driver._sequence_progress = progress
        await asyncio.sleep(0.1)
      follow.cancel()
      await asyncio.gather(follow, return_exceptions=True)
    self.assertEqual(self.states(), [[GREEN] * 10 + [OFF] * 10, GREEN])

  async def test_the_dispense_done_blinks_the_full_bar(self):
    await self.bar._show_dispense_done()
    self.assertEqual(self.states(), [GREEN] + [OFF, GREEN] * 3)
    self.assertEqual(
      self.led_bar_sleep.await_args_list[-6:],
      [mock.call(self.bar, seconds=DISPENSE_DONE_BLINK)] * 6,
    )


if __name__ == "__main__":
  unittest.main()
