"""PrepSimulationDriver: answers from its recordings and its resource model, at the wire."""

import asyncio

import pytest

from pylabrobot.hamilton.prep import Prep, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.configuration import read_configuration
from pylabrobot.hamilton.prep.driver.errors import PrepMethodNotFoundError
from pylabrobot.hamilton.prep.driver.simulator import (
  FIRMWARE_TREE_V3_0_20,
  RECORDING_PREP,
  RECORDING_PREP_HEAD8,
)
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.resources import Coordinate
from pylabrobot.resources.hamilton import PrepDeck


def test_firmware_tree_is_selectable():
  """A method the recorded firmware lacks is refused as that firmware refuses it."""

  async def _run() -> None:
    v1 = PrepSimulationDriver(deck=PrepDeck())
    await v1.setup()
    with pytest.raises(PrepMethodNotFoundError, match="IsParked"):
      await v1.is_parked()
    await v1.stop()

    v3 = PrepSimulationDriver(deck=PrepDeck(), firmware_tree_json=FIRMWARE_TREE_V3_0_20)
    await v3.setup()
    assert await v3.is_parked() is False
    await v3.stop()

  asyncio.run(_run())


def test_a_read_nothing_answers_is_refused():
  """A read the simulator does not answer comes back as a firmware exception, not a made-up value."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    with pytest.raises(HoiError):
      await p.request_bootloader_version()
    await p.stop()

  asyncio.run(_run())


def test_moves_update_the_model_and_reads_answer_from_it():
  """A move puts the channel where it was sent in the resource model, and a read finds it there."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    await p.pipettes.move_to_location(Coordinate(150.0, 360.0, 160.0), use_channels=0)
    point = p.pipettes.get_reference_point_location(0)
    assert point is not None
    assert (point.x, point.y, point.z) == pytest.approx((150.0, 360.0, 160.0))
    locations = await p.pipettes.request_locations()
    assert (locations[0].x, locations[0].y, locations[0].z) == pytest.approx((150.0, 360.0, 160.0))
    assert await p.x_arm.request_position() == pytest.approx(150.0)
    await p.stop()

  asyncio.run(_run())


def test_initializes_once_when_switched_on():
  """A device switched on reports itself uninitialized until MLPrep.Initialize has run."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck())
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.setup()
    assert "PrepInitialize" in sent
    assert await p.request_initialization_status() is True
    await p.stop()

  asyncio.run(_run())


def test_setup_places_the_teaching_needle_and_waste_positions_where_the_device_reports_them():
  """The deck's defaults give way to the device's deck and waste sites at setup."""

  async def _run() -> None:
    deck = PrepDeck()
    needle = deck.teaching_needle_spot
    waste = deck.waste_positions["waste_front"]
    assert needle is not None and needle.location is not None
    height = needle.location.z
    needle.location = Coordinate(0.0, 0.0, height)
    waste.location = Coordinate(0.0, 0.0, 0.0)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    recorded = read_configuration(RECORDING_PREP)["device"]
    site = next(s for s in recorded.deck_sites if (s.length, s.width) == (6.0, 6.0))
    placed = needle.get_location_wrt(deck)
    assert (placed.x, placed.y, needle.location.z) == pytest.approx(
      (site.left_bottom_front_x, site.left_bottom_front_y, height)
    )
    front = next(
      s for s in recorded.waste_sites if s.index == int(PrepCmd.ChannelIndex.FrontChannel)
    )
    assert (waste.location.x, waste.location.y, waste.location.z) == pytest.approx(
      (front.x_position, front.y_position, front.z_position)
    )
    await p.stop()

  asyncio.run(_run())


def test_head8_recording_has_a_head8():
  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck(), declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None
    assert p.format_setup_summary().endswith("8-channel head: installed")
    sent = await p.send_command(PrepCmd.PrepGetPresentChannels())
    assert PrepCmd.ChannelIndex.MPHChannel in sent.channels
    await p.stop()

  asyncio.run(_run())


def test_prep_factory_builds_a_simulated_device():
  async def _run() -> None:
    prep = Prep(simulation=True)
    assert isinstance(prep.driver, PrepSimulationDriver)
    await prep.setup()
    assert prep.pipettes is not None
    await prep.stop()

  asyncio.run(_run())


def test_the_arm_reference_line_spans_the_channels_combined_y_ranges():
  """The arm declares the Y its channels reach between them, from its own front edge."""

  async def _run() -> None:
    deck = PrepDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    arm = deck.get_resource(deck.prefixed("x_arm"))
    front = arm.get_location_wrt(deck).y
    ranges = [c.y_range for c in p.pipettes.configuration.channels if c.y_range is not None]
    low, high = arm.reference_point["y_range"]  # type: ignore[attr-defined]
    assert (low + front, high + front) == pytest.approx(
      (min(r[0] for r in ranges), max(r[1] for r in ranges))
    )
    await p.stop()

  asyncio.run(_run())
