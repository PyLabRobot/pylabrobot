"""Tests for the Prep deck light: the colours it takes, and what it sends."""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.lights import as_wrgb, mix
from pylabrobot.resources.hamilton import PrepDeck


def _record_send(prep: PrepDriver) -> List[Any]:
  captured: List[Any] = []
  orig_send = prep.send_command

  async def recording(command, **kw):
    captured.append(command)
    return await orig_send(command, **kw)

  prep.send_command = recording  # type: ignore[method-assign, assignment]
  return captured


def _colors(captured: List[Any]) -> List[Any]:
  return [
    (c.white, c.red, c.green, c.blue) for c in captured if isinstance(c, PrepCmd.PrepSetDeckLight)
  ]


async def _prep() -> PrepSimulationDriver:
  p = PrepSimulationDriver(deck=PrepDeck())
  await p.setup()
  return p


def test_a_colour_is_a_name_a_hex_string_or_channels():
  """Every form names the same white, red, green and blue."""
  assert as_wrgb("red") == (0, 255, 0, 0)
  assert as_wrgb("#ff8800") == (0, 255, 136, 0)
  assert as_wrgb("#80ff8800") == (128, 255, 136, 0)
  assert as_wrgb((255, 136, 0)) == (0, 255, 136, 0)
  assert as_wrgb((128, 255, 136, 0)) == (128, 255, 136, 0)


def test_a_colour_that_is_no_colour_is_refused():
  """A name that is not a colour, too few channels, and a channel past 255."""
  for color in ("mauve", "#ff88", (255, 136), (0, 256, 0, 0)):
    with pytest.raises(ValueError):
      as_wrgb(color)


def test_the_light_is_built_at_setup_only_when_the_device_has_one():
  """The device answers whether it has a light, and the feature follows that answer."""

  async def run():
    p = await _prep()
    assert await p.request_deck_light_installed() is True
    assert p.lights is not None
    await p.stop()

  asyncio.run(run())


def test_turning_the_light_off_darkens_every_led():
  """`turn_off` is (0, 0, 0, 0), all four channels."""

  async def run():
    p = await _prep()
    captured = _record_send(p)
    assert p.lights is not None
    await p.lights.turn_off()
    assert _colors(captured) == [(0, 0, 0, 0)]
    await p.stop()

  asyncio.run(run())


def test_brightness_is_how_bright_the_colour_looks():
  """A percentage is gamma-mapped onto the channels, so half brightness is well under half."""

  async def run():
    p = await _prep()
    captured = _record_send(p)
    assert p.lights is not None
    await p.lights.set_color((0, 200, 100, 0), brightness=100)
    assert _colors(captured) == [(0, 200, 100, 0)]  # 100 is the colour exactly as given
    await p.lights.set_color((0, 200, 100, 0), brightness=50)
    assert _colors(captured)[-1] == (0, 44, 22, 0)  # 0.5 ** 2.2
    await p.lights.turn_on(brightness=20)
    assert _colors(captured)[-1] == (7, 0, 0, 0)
    with pytest.raises(ValueError):
      await p.lights.set_color("red", brightness=101)
    await p.stop()

  asyncio.run(run())


def test_two_colours_blend_channel_by_channel():
  """The first at 0, the second at 1, and halfway between at 0.5."""
  assert mix("red", "orange", 0) == (0, 255, 0, 0)
  assert mix("red", "orange", 1) == (0, 255, 80, 0)
  assert mix("red", "orange", 0.5) == (0, 255, 40, 0)


def test_the_error_pulse_swells_red_into_orange_until_it_is_turned_off():
  """It returns at once, swells red towards orange, and stops when the light is turned off."""

  async def run():
    p = await _prep()
    captured = _record_send(p)
    assert p.lights is not None
    await p.lights.animate_error_pulse()
    await asyncio.sleep(0.35)
    pulsed = _colors(captured)
    assert len(pulsed) > 1
    # Red leads and green follows it into orange; the white and blue LEDs stay dark.
    assert all(white == blue == 0 and 0 < red and green < red for white, red, green, blue in pulsed)
    assert len(set(pulsed)) > 1

    await p.lights.turn_off()
    await asyncio.sleep(0.25)
    assert _colors(captured)[-1] == (0, 0, 0, 0)
    await p.stop()

  asyncio.run(run())


def test_a_pulse_given_seconds_darkens_the_light_when_they_are_up():
  """A duration ends the pulse itself, with the light off."""

  async def run():
    p = await _prep()
    captured = _record_send(p)
    assert p.lights is not None
    await p.lights.animate_error_pulse(duration=0.3)
    await asyncio.sleep(0.6)
    assert _colors(captured)[-1] == (0, 0, 0, 0)
    assert len(_colors(captured)) > 2
    await p.stop()

  asyncio.run(run())
