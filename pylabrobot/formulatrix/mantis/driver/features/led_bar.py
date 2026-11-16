"""The LED bar: the v4's status light on the LC3 board (board 12), one RGB565 colour per LED.

It shows a static colour on each LED, or plays an animation the firmware holds. While an action
runs, the bar shows its colour (the bar's ``*_color`` attributes); between actions, the idle
animation.
"""

import asyncio
import logging
import struct
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Dict, List, Optional, Sequence, Tuple, Union

from ..framing import LC3_BOARD

if TYPE_CHECKING:
  from ..master import MantisDriver

logger = logging.getLogger(__name__)

# The firmware's animations: name -> (first frame, last frame), played repeating. "wave" is the
# idle animation.
EFFECTS: Dict[str, Tuple[int, int]] = {
  "wave": (0, 86),
  "fatal_error": (800, 802),
  "unconnected": (950, 968),
}
# The RGB565 value of each named colour.
LED_COLOR_CODES: Dict[str, int] = {
  "off": 0x0000,
  "blue": 0x001F,
  "green": 0x07E0,
  "yellow": 0xFFE0,
  "white": 0xFFFF,
  "red": 0xF800,
  "magenta": 0xF81F,
}
# A colour: an RGB565 value, a name of ``LED_COLOR_CODES`` or a 24-bit web hex ("#FF0080").
Color = Union[int, str]
# How long the bar shows ``setup_done_color`` before the idle animation, in s.
SETUP_DONE_HOLD = 1.0
# The end of a dispense blinks the full progress bar this many times, this long off and on, in s.
DISPENSE_DONE_BLINKS = 3
DISPENSE_DONE_BLINK = 0.35
# How often the progress bar looks at the sequencer's progress, in s.
FILL_INTERVAL = 0.2
# Sequencer item ids run 1 to SEQUENCE_ID_COUNT and wrap.
SEQUENCE_ID_COUNT = 32767


def _compute_rgb565(*, color: Color) -> int:
  """One colour's RGB565 value: the top 5, 6 and 5 bits of a web hex's R, G and B.

  Args:
    color: an RGB565 value, a name of ``LED_COLOR_CODES`` or a web hex ("#FF0080" or "FF0080").

  Raises:
    ValueError: if ``color`` is none of these.
  """
  if isinstance(color, int):
    if not 0 <= color <= 0xFFFF:
      raise ValueError(f"an RGB565 value is 0 to 0xFFFF, got {color:#x}")
    return color
  if color in LED_COLOR_CODES:
    return LED_COLOR_CODES[color]
  digits = color[1:] if color.startswith("#") else color
  if len(digits) != 6 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
    raise ValueError(
      f"{color!r} is neither a colour of {sorted(LED_COLOR_CODES)} nor a web hex like '#FF0080'"
    )
  value = int(digits, 16)
  red, green, blue = (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
  return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


class LedBar:
  """The v4's LED bar: ``number_of_leds`` LEDs, each showing one RGB565 colour, or an animation.

  Each ``*_color`` attribute is the colour shown for one action, checked when it is set; each bar
  has its own.
  """

  # Where the ``*_color`` properties start.
  _setup_color: Color = "blue"
  _setup_done_color: Color = "green"
  _error_color: Color = "red"
  _dispense_progress_color: Color = "green"

  def __init__(self, *, driver: "MantisDriver", number_of_leds: int, show_actions: bool):
    """
    Args:
      driver: the driver it sends through.
      number_of_leds: the unit's LED count, as declared.
      show_actions: whether actions show their colour, and the idle animation between them.
    """
    self._driver = driver
    self.number_of_leds = number_of_leds
    self.show_actions = show_actions
    # The actions running, outermost first: (action, whether actions inside it change the bar).
    self._running_actions: List[Tuple[str, bool]] = []
    # The LEDs ``show_progress`` lit last, while the bar still shows them.
    self._progress_shown: Optional[int] = None

  @property
  def setup_color(self) -> Color:
    """The colour shown while setup runs."""
    return self._setup_color

  @setup_color.setter
  def setup_color(self, value: Color) -> None:
    _compute_rgb565(color=value)
    self._setup_color = value

  @property
  def setup_done_color(self) -> Color:
    """The colour shown for ``SETUP_DONE_HOLD`` after a setup."""
    return self._setup_done_color

  @setup_done_color.setter
  def setup_done_color(self, value: Color) -> None:
    _compute_rgb565(color=value)
    self._setup_done_color = value

  @property
  def error_color(self) -> Color:
    """The colour shown after an action that failed, until the next action starts."""
    return self._error_color

  @error_color.setter
  def error_color(self, value: Color) -> None:
    _compute_rgb565(color=value)
    self._error_color = value

  @property
  def dispense_progress_color(self) -> Color:
    """The colour of the LEDs ``show_progress`` lights."""
    return self._dispense_progress_color

  @dispense_progress_color.setter
  def dispense_progress_color(self, value: Color) -> None:
    _compute_rgb565(color=value)
    self._dispense_progress_color = value

  async def _unchecked_fw_stop_animator(self) -> None:
    """StopAnimator (board 12, command 16) on strip 0, sent once."""
    await self._driver._send_command(
      board=LC3_BOARD, command=16, payload=struct.pack("<H", 0), attempts=1
    )

  async def _unchecked_fw_start_animator(self, *, lowest_index: int, highest_index: int) -> None:
    """StartAnimator (board 12, command 17) on strip 0, repeating, sent once.

    Args:
      lowest_index: the first frame.
      highest_index: the last frame.
    """
    await self._driver._send_command(
      board=LC3_BOARD,
      command=17,
      payload=struct.pack("<HHHH", 0, lowest_index, highest_index, 1),
      attempts=1,
    )

  async def _unchecked_fw_set_rgb_led(self, *, colors: List[int]) -> None:
    """SetRgbLed (board 12, command 13) on strip 0, sent once; not shown while an animation plays.

    Args:
      colors: one RGB565 value per LED.
    """
    await self._driver._send_command(
      board=LC3_BOARD,
      command=13,
      payload=struct.pack(f"<HH{len(colors)}H", 0, len(colors), *colors),
      attempts=1,
    )

  async def start_animation(self, *, effect: str = "wave") -> None:
    """Play one of ``EFFECTS``, repeating, until a colour is set or the animation is stopped.

    Args:
      effect: a key of ``EFFECTS``.

    Raises:
      ValueError: if ``effect`` is not in ``EFFECTS``; nothing is sent.
    """
    if effect not in EFFECTS:
      raise ValueError(f"effect must be one of {sorted(EFFECTS)}, got {effect!r}")
    lowest_index, highest_index = EFFECTS[effect]
    self._progress_shown = None
    await self._unchecked_fw_start_animator(lowest_index=lowest_index, highest_index=highest_index)

  async def stop_animation(self) -> None:
    """Stop the animation; the bar holds what it shows."""
    await self._unchecked_fw_stop_animator()

  async def set_color(self, *, color: Union[Color, Sequence[Color]]) -> None:
    """Show one colour on every LED, or one colour per LED; any animation is stopped first.

    Args:
      color: one ``Color`` for every LED, or ``number_of_leds`` of them, one per LED.

    Raises:
      ValueError: if a colour is not a ``Color``, or a sequence does not hold ``number_of_leds``
        colours; nothing is sent.
    """
    if isinstance(color, (int, str)):
      colors = [_compute_rgb565(color=color)] * self.number_of_leds
    else:
      colors = [_compute_rgb565(color=one) for one in color]
      if len(colors) != self.number_of_leds:
        raise ValueError(f"one colour per LED: {self.number_of_leds} colours, got {len(colors)}")
    # A colour sent while an animation plays is not shown.
    self._progress_shown = None
    await self.stop_animation()
    await self._unchecked_fw_set_rgb_led(colors=colors)

  async def show_progress(self, *, done: int, total: int) -> None:
    """Light ``round(number_of_leds * done / total)`` LEDs from the left in
    ``dispense_progress_color``, at least one once ``done`` > 0, the rest off; nothing is sent when
    that many are already lit.

    Each frame holds the link about 62 ms (measured on a v4): show at most ``number_of_leds``
    frames per action.

    Args:
      done: the steps done, 0 to ``total``.
      total: the steps, at least 1.

    Raises:
      ValueError: if ``total`` is below 1, or ``done`` below 0 or above ``total``; nothing is sent.
    """
    if total < 1:
      raise ValueError(f"total must be at least 1, got {total}")
    if not 0 <= done <= total:
      raise ValueError(f"done must be 0 to {total}, got {done}")
    lit = round(self.number_of_leds * done / total)
    if done > 0:
      lit = max(lit, 1)  # any progress shows: a first step of 400 still lights one LED
    if lit == self._progress_shown:
      return
    await self.set_color(
      color=[self.dispense_progress_color] * lit + ["off"] * (self.number_of_leds - lit)
    )
    self._progress_shown = lit

  async def turn_off(self) -> None:
    """Turn every LED off; no action shows its colour until the next one starts."""
    try:
      await self.set_color(color="off")
    finally:
      self._running_actions.clear()

  async def _sleep(self, *, seconds: float) -> None:
    """Hold the bar as it is.

    Args:
      seconds: how long, in s.
    """
    await asyncio.sleep(seconds)

  async def _show(self, *, key: str) -> None:
    """Show an action's colour, or the idle animation for "idle"; a fault only warns.

    Args:
      key: "setup", "setup_done", "error" or "idle".
    """
    colors: Dict[str, Color] = {
      "setup": self.setup_color,
      "setup_done": self.setup_done_color,
      "error": self.error_color,
    }
    try:
      if key == "idle":
        await self.start_animation(effect="wave")
      else:
        await self.set_color(color=colors[key])
    except Exception:
      logger.warning("[%s] the LED bar did not show %r", self._driver.name, key, exc_info=True)

  async def _show_setup_done(self) -> None:
    """Show ``setup_done_color`` for ``SETUP_DONE_HOLD``; leaving setup then shows idle."""
    await self._show(key="setup_done")
    await self._sleep(seconds=SETUP_DONE_HOLD)

  async def _show_fill(self, *, done: int, total: int) -> None:
    """``show_progress``, where a fault only warns.

    Args:
      done: the steps done.
      total: the steps.
    """
    try:
      await self.show_progress(done=done, total=total)
    except Exception:
      logger.warning("[%s] the LED bar did not show the progress", self._driver.name, exc_info=True)

  async def _follow_fill(self, *, well_ends: List[int], total: int) -> None:
    """Fill the bar as the sequencer reports each well's last item played, until cancelled.

    Args:
      well_ends: each well's last queued item so far; the dispense appends to it.
      total: the wells of the dispense.
    """
    shown = 0
    while True:
      latest = self._driver._sequence_progress
      done = 0
      if latest is not None:
        # An item has played when the latest reported is at or after it, the ids wrapping.
        half = SEQUENCE_ID_COUNT // 2
        done = sum((latest - end) % SEQUENCE_ID_COUNT < half for end in well_ends)
      if done != shown:
        await self._show_fill(done=done, total=total)
        shown = done
      await asyncio.sleep(FILL_INTERVAL)

  async def _show_dispense_done(self) -> None:
    """The full progress bar, then ``DISPENSE_DONE_BLINKS`` blinks off and on."""
    await self._show_fill(done=1, total=1)
    for _ in range(DISPENSE_DONE_BLINKS):
      await self._show_fill(done=0, total=1)
      await self._sleep(seconds=DISPENSE_DONE_BLINK)
      await self._show_fill(done=1, total=1)
      await self._sleep(seconds=DISPENSE_DONE_BLINK)

  @asynccontextmanager
  async def _show_action(
    self, *, action: str, show_inner_actions: bool = True, only_when_idle: bool = False
  ) -> AsyncIterator[None]:
    """Show ``action``'s colour while the block runs.

    Leaving it shows the enclosing action's colour, or idle when none runs. An action that fails
    leaves ``error_color`` until the next action starts; a cancelled one changes nothing.

    Args:
      action: a key ``_show`` takes, other than "idle".
      show_inner_actions: whether actions inside the block change the bar.
      only_when_idle: change the bar only when no other action runs.
    """
    running = self._running_actions
    if running and (only_when_idle or not running[-1][1]):
      yield
      return
    running.append((action, show_inner_actions))
    await self._show(key=action)
    try:
      yield
    except BaseException as error:
      running.pop()
      if isinstance(error, Exception) and not running:
        await self._show(key="error")
      raise
    running.pop()
    await self._show(key=running[-1][0] if running else "idle")
