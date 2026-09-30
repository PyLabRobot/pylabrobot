"""Hamilton Prep deck light: the RGBW panel over the deck."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import TYPE_CHECKING, Callable, Dict, Optional, Sequence, Tuple, Union

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver

logger = logging.getLogger(__name__)

# A colour, in any of the forms the light takes it in: a name from `COLORS`, a hex string
# ("#ff8800", or "#80ff8800" as #WWRRGGBB), (red, green, blue), or (white, red, green, blue).
Color = Union[str, Sequence[int]]

# What an animation shows at one moment: the colour, and what percentage of it to light, given the
# seconds the animation has been running.
Frame = Callable[[float], Tuple[Color, float]]

# Named colours as (white, red, green, blue), each channel 0 to 255. The white LEDs light the deck;
# the three coloured ones tint it.
COLORS: Dict[str, Tuple[int, int, int, int]] = {
  "off": (0, 0, 0, 0),
  "white": (255, 0, 0, 0),
  "warm white": (255, 60, 20, 0),
  "red": (0, 255, 0, 0),
  "orange": (0, 255, 80, 0),
  "deep orange": (0, 255, 45, 0),
  "yellow": (0, 255, 200, 0),
  "green": (0, 0, 255, 0),
  "cyan": (0, 0, 255, 255),
  "turquoise": (0, 0, 255, 180),
  "blue": (0, 0, 0, 255),
  "purple": (0, 110, 0, 255),
  "magenta": (0, 255, 0, 255),
  "pink": (80, 255, 0, 120),
}


# How the LEDs' output maps to how bright they look. The eye answers roughly to the 2.2nd root of
# what an LED is driven with, so a brightness given here is raised to this before it is sent: 50
# then looks half as bright, rather than looking nearly full.
GAMMA = 2.2


def _log_animation_end(task: "asyncio.Task[None]") -> None:
  """Say what became of an animation: a background task's error is otherwise never seen."""
  if not task.cancelled() and task.exception() is not None:
    logger.error("the deck light animation stopped: %s", task.exception())


def as_wrgb(color: Color) -> Tuple[int, int, int, int]:
  """A colour in any accepted form, as (white, red, green, blue).

  Args:
    color: a name from `COLORS`, a hex string, (red, green, blue), or (white, red, green, blue).

  Raises:
    ValueError: If the colour is none of those forms, or a channel is outside 0 to 255.
  """
  if isinstance(color, str):
    name = color.strip().lower()
    if name in COLORS:
      return COLORS[name]
    digits = name[1:] if name.startswith("#") else name
    if len(digits) in (6, 8) and all(digit in "0123456789abcdef" for digit in digits):
      channels = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
      return as_wrgb(channels)
    raise ValueError(f"{color!r} is neither a hex colour nor one of {', '.join(sorted(COLORS))}")
  channels = list(color)
  if len(channels) == 3:
    channels = [0] + channels
  if len(channels) != 4:
    raise ValueError(f"a colour is 3 or 4 channels, {color!r} is {len(channels)}")
  for value in channels:
    if not 0 <= value <= 255:
      raise ValueError(f"each channel is 0 to 255, {color!r} has {value}")
  white, red, green, blue = channels
  return (white, red, green, blue)


def mix(first: Color, second: Color, fraction: float) -> Tuple[int, int, int, int]:
  """Two colours blended channel by channel: `first` at 0, `second` at 1.

  Args:
    first: the colour at 0.
    second: the colour at 1.
    fraction: how far from the first to the second, 0 to 1.
  """
  start, end = as_wrgb(first), as_wrgb(second)
  white, red, green, blue = (round(a + (b - a) * fraction) for a, b in zip(start, end))
  return (white, red, green, blue)


def flicker(period: float) -> Frame:
  """A new random colour every `period` seconds.

  Args:
    period: seconds one colour stands for.
  """
  showing: Dict[int, Tuple[int, int, int, int]] = {}

  def frame(elapsed: float) -> Tuple[Color, float]:
    step = int(elapsed / period)
    if step not in showing:
      showing.clear()
      white, red, green, blue = (random.randint(1, 255) for _ in range(4))
      showing[step] = (white, red, green, blue)
    return showing[step], 100.0

  return frame


def swell(
  color: Color, period: float, low: float, high: float, peak_color: Optional[Color] = None
) -> Frame:
  """A colour swelling from `low` to `high` percent of how bright it looks, and back.

  A raised cosine, which turns without a corner at either end, unlike a triangle.

  Args:
    color: the colour at its dimmest.
    period: seconds for one swell, up and back down.
    low: the percentage it dims to.
    high: the percentage it brightens to.
    peak_color: the colour at its brightest, blended into as it swells. None keeps one colour.
  """

  def frame(elapsed: float) -> Tuple[Color, float]:
    fraction = (1 - math.cos(2 * math.pi * elapsed / period)) / 2
    shade = color if peak_color is None else mix(color, peak_color, fraction)
    return shade, low + (high - low) * fraction

  return frame


class Lights:
  """The deck light. Built at setup, as `prep.lights`, if the device has one."""

  # How long setup stands at its ready colour before darkening the deck, in seconds.
  default_ready_seconds: float = 1.5

  def __init__(self, driver: "PrepDriver") -> None:
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver
    self._animation: Optional["asyncio.Task[None]"] = None
    # Whether the animation being cancelled is making way for a colour this light is about to set.
    self._handing_over = False
    # What a command uses when it names nothing. Set them to change what this light does.
    self.default_brightness: float = 100.0
    # How long one colour stands before the next, while an animation runs. A set takes about a
    # millisecond, so 50 a second is well inside what the device takes, and finer than an eye
    # resolves.
    self.default_animation_step: float = 0.02

  # ----------------------------------------
  # The light
  # ----------------------------------------

  async def _set(self, color: Color, brightness: Optional[float] = None) -> None:
    """Send one colour, leaving any animation running.

    Raises:
      ValueError: As `set_color`.
    """
    brightness = self.default_brightness if brightness is None else brightness
    if not 0.0 <= brightness <= 100.0:
      raise ValueError(f"brightness is a percentage, 0 to 100, is {brightness}")
    scale = (brightness / 100) ** GAMMA
    white, red, green, blue = (round(c * scale) for c in as_wrgb(color))
    await self._driver.send_command(
      PrepCmd.PrepSetDeckLight(white=white, red=red, green=green, blue=blue)
    )

  async def request_color(self) -> Tuple[int, int, int, int]:
    """The colour the light stands at, as (white, red, green, blue), each 0 to 255.

    Raises:
      ValueError: If the device does not answer.
    """
    result = await self._driver.send_command(PrepCmd.PrepGetDeckLight())
    if result is None:
      raise ValueError("the device did not answer what its deck light stands at")
    return (result.white, result.red, result.green, result.blue)

  def stop_animation(self) -> None:
    """Stop an animation, if one is running, for a colour to be set in its place.

    The light stays at the colour it reached, for whoever stopped it to set the next one. An
    animation cancelled by anything else darkens the deck instead, since nobody is coming.
    """
    if self._animation is not None and not self._animation.done():
      self._handing_over = True
      self._animation.cancel()
    self._animation = None

  # ----------------------------------------
  # Colour
  # ----------------------------------------

  async def set_color(self, color: Color, brightness: Optional[float] = None) -> None:
    """Set the light to a colour, ending any animation. Stays set until changed.

    Args:
      color: a name from `COLORS`, a hex string ("#ff8800", or "#80ff8800" as #WWRRGGBB),
        (red, green, blue), or (white, red, green, blue).
      brightness: how bright to make the colour look, 0 to 100, or None for `default_brightness`.
        100 is the colour exactly as given; below that the channels fall away faster than the
        percentage, because that is what the eye answers to, so a read-back reads lower.

    Raises:
      ValueError: If the colour is not one of those forms, a channel is outside 0 to 255, or
        `brightness` is outside 0 to 100.
    """
    self.stop_animation()
    await self._set(color, brightness)

  async def turn_on(self, brightness: Optional[float] = None) -> None:
    """Light the deck white.

    Args:
      brightness: how bright to make the white LEDs look, 0 to 100, or None for
        `default_brightness`.
    """
    await self.set_color("white", brightness=brightness)

  async def turn_off(self) -> None:
    """Darken every LED, ending any animation."""
    await self.set_color("off")

  # ----------------------------------------
  # Animation
  # ----------------------------------------

  async def _run(self, frame: Frame, duration: Optional[float]) -> None:
    """Show an animation off the clock, not off a frame count, until its time is up.

    A frame the light already stands at is not sent again, so a held colour costs one command.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    showing: Optional[Tuple[Color, float]] = None
    try:
      while duration is None or loop.time() - started < duration:
        shown = frame(loop.time() - started)
        if shown != showing:
          await self._set(*shown)
          showing = shown
        await asyncio.sleep(self.default_animation_step)
    except asyncio.CancelledError:
      # Cancelled from outside - a stopped protocol, an interrupted notebook, a loop shutting down.
      # Nobody is coming to set a colour, so the deck is darkened rather than left standing at the
      # frame this stopped on. The darkening goes out on its own, since a cancelled task cannot
      # wait for it.
      if not self._handing_over:
        asyncio.ensure_future(self._set("off"))
      self._handing_over = False
      raise
    await self._set("off")

  async def animate(self, frame: Frame, duration: Optional[float] = None) -> None:
    """Run an animation in the background, in place of the one running.

    Args:
      frame: what to show at a moment, as `swell` builds.
      duration: how many seconds to run for, after which the light goes off. None runs until the
        light is turned off or set to something else.
    """
    self.stop_animation()
    self._animation = asyncio.create_task(self._run(frame, duration))
    self._animation.add_done_callback(_log_animation_end)

  async def wait_for_animation(self) -> None:
    """Hold until the animation running has ended, if one is. A stopped animation is not waited on."""
    animation = self._animation
    if animation is None:
      return
    try:
      await animation
    except asyncio.CancelledError:
      if not animation.cancelled():
        raise

  async def hold(self, color: Color, duration: float, brightness: Optional[float] = None) -> None:
    """Show a colour for a while, then darken. Holds until its time is up.

    Args:
      color: the colour to hold.
      duration: how many seconds to hold it for.
      brightness: how bright to make it look, 0 to 100, or None for `default_brightness`.
    """
    await self.set_color(color, brightness)
    await asyncio.sleep(duration)
    await self.turn_off()

  async def animate_error_pulse(
    self,
    duration: Optional[float] = None,
    color: Color = "red",
    peak_color: Optional[Color] = "deep orange",
    period: float = 2.0,
    low: float = 30.0,
    high: float = 100.0,
  ) -> None:
    """Pulse the deck red, swelling into a deep orange, to be seen across a room.

    The pulse runs in the background, so this returns as soon as it is on.

    Args:
      duration: how many seconds to pulse for, after which the light goes off. None pulses until
        the light is turned off or set to something else.
      color: the colour at its dimmest.
      peak_color: the colour at its brightest, blended into as it swells. None keeps one colour.
      period: seconds for one swell, up and back down.
      low: what percentage of how bright it looks it dims to.
      high: what percentage it brightens to.
    """
    await self.animate(
      swell(color, period=period, low=low, high=high, peak_color=peak_color), duration
    )

  async def disco(self, duration: Optional[float] = 15, step: float = 0.1) -> None:
    """Easter egg: random colours, and then the deck goes dark.

    Runs in the background, so this returns as soon as it is on.

    Args:
      duration: how many seconds to cycle for, after which the light goes off. None cycles until
        the light is turned off or set to something else.
      step: how many seconds one colour stands for.
    """
    await self.animate(flicker(step), duration)
