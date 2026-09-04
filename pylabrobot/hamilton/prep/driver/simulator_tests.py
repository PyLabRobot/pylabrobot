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
from pylabrobot.hamilton.transport.tcp.session import SessionState
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.hamilton import PrepDeck, hamilton_96_tiprack_50uL_NTR
from pylabrobot.resources.trash import Trash
from pylabrobot.testing.concurrency import AnyioTestBase


class TestScopedPrep(AnyioTestBase):
  async def test_feature_cleanup_keeps_pipettes_before_head8(self) -> None:
    """Feature teardown runs in firmware's existing order while the link is active."""
    driver = PrepSimulationDriver(deck=PrepDeck(), declared_configuration_json=RECORDING_PREP_HEAD8)
    called: list[str] = []
    async with driver:
      assert driver.pipettes is not None and driver.head8 is not None
      stop_pipettes = driver.pipettes._on_stop
      stop_head8 = driver.head8._on_stop

      async def record_pipettes() -> None:
        assert driver.io.is_connected
        called.append("pipettes")
        await stop_pipettes()

      async def record_head8() -> None:
        assert driver.io.is_connected
        called.append("head8")
        await stop_head8()

      driver.pipettes._on_stop = record_pipettes  # type: ignore[method-assign]
      driver.head8._on_stop = record_head8  # type: ignore[method-assign]
    assert called == ["pipettes", "head8"]

  async def test_uncertain_session_exit_sends_no_safety_commands(self) -> None:
    """An unknown terminal outcome forbids further diagnostics or Z movement."""
    driver = PrepSimulationDriver(deck=PrepDeck())
    sent: list[object] = []
    send = driver.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    driver.send_command = record  # type: ignore[method-assign]
    async with driver:
      driver._core_gripper_arm = driver.x_arm
      driver.io._session.fail(ConnectionError("unknown terminal outcome"))
      assert driver.io.connection_info.state is SessionState.UNCERTAIN
      before_exit = len(sent)
    assert len(sent) == before_exit
    assert driver.io.connection_info.state is SessionState.CLOSED
    assert driver._core_gripper_arm is None

  async def test_failed_resource_entry_reconciles_z_before_closing(self) -> None:
    """A failed entry after initialization still retracts channels while linked."""
    driver = PrepSimulationDriver(deck=PrepDeck())

    async def fail_after_lowering() -> None:
      assert driver.pipettes is not None
      await driver.pipettes.move_tool_bottom_to_z_positions({0: 120.0})
      raise RuntimeError("resource modelling failed")

    create_resources = driver._create_capability_resources
    driver._create_capability_resources = fail_after_lowering  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="resource modelling failed"):
      async with driver:
        pass
    assert driver.io.connection_info.state is SessionState.CLOSED
    driver._create_capability_resources = create_resources  # type: ignore[method-assign]
    async with driver.lifespan(skip_raise_to_z_safety=True):
      assert await driver.features_below_safe_z() == []

  async def test_device_owns_simulated_driver_and_closes_session(self) -> None:
    """Device exit releases its driver's session without ever opening a real Socket."""
    device = Prep(simulation=True)
    async with device:
      assert device.driver.io.is_connected
      session = device.driver.io._session
    assert not device.driver.io.is_connected
    assert session.state.name == "CLOSED"

  async def test_skip_safety_option_preserves_lowered_channels(self) -> None:
    """The scoped stop option leaves Z low, and a later entry raises it."""
    deck = PrepDeck()
    driver = PrepSimulationDriver(deck=deck)
    async with driver.lifespan(skip_raise_to_z_safety=True):
      assert driver.pipettes is not None
      await driver.pipettes.move_tool_bottom_to_z_positions({0: 120.0})
    again = PrepSimulationDriver(deck=deck)
    async with again:
      assert await again.features_below_safe_z() == []


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


@pytest.mark.parametrize("name_prefix", [None, "prep_a"])
def test_setup_places_the_teaching_needle_and_waste_positions_where_the_device_reports_them(
  name_prefix,
):
  """The deck's defaults give way to the device's deck and waste sites at setup."""

  async def _run() -> None:
    deck = PrepDeck(name_prefix=name_prefix)
    needle = deck.get_resource(deck.get_component_name("teaching_tip"))
    waste = deck.get_resource(deck.get_component_name("waste_front"))
    assert needle.location is not None
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
    arm = deck.get_resource("x_arm")
    front = arm.get_location_wrt(deck).y
    ranges = [c.y_range for c in p.pipettes.configuration.channels if c.y_range is not None]
    low, high = arm.reference_point["y_range"]  # type: ignore[attr-defined]
    assert (low + front, high + front) == pytest.approx(
      (min(r[0] for r in ranges), max(r[1] for r in ranges))
    )
    await p.stop()

  asyncio.run(_run())


def test_named_preps_share_a_resource_tree_and_reuse_their_components():
  """Each device owns distinct names, including resources created during repeated setup."""

  async def _run() -> None:
    bench = Resource(name="bench", size_x=2000, size_y=1000, size_z=1000)
    for index, name in enumerate(("prep_a", "prep_b")):
      prep = Prep(name=name, simulation=True)
      assert prep.deck.name == f"{name}_deck"
      assert all(child.name.startswith(f"{name}_") for child in prep.get_all_children())
      bench.assign_child_resource(prep, location=Coordinate(index * 600, 0, 0))
      await prep.setup()
      assert prep.x_arm is not None and prep.x_arm.resource is not None
      assert prep.pipettes is not None
      assert prep.x_arm.resource.name == f"{name}_x_arm"
      assert prep.pipettes.resources[0].name == f"{name}_pipette_channel_0"
      components = prep.get_all_children()
      assert all(child.name.startswith(f"{name}_") for child in components)
      assert all(bench.get_resource(child.name) is child for child in components)
      await prep.stop()
      await prep.setup()
      assert [id(child) for child in prep.get_all_children()] == [id(child) for child in components]
      await prep.stop()

  asyncio.run(_run())


def test_named_prep_drops_tips_at_its_own_waste_positions():
  """Tip disposal resolves prefixed waste names while caller-provided labware keeps its name."""

  async def _run() -> None:
    prep = Prep(name="prep_a", simulation=True)
    rack = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    prep.deck[3] = rack
    await prep.setup()
    assert prep.pipettes is not None
    await prep.pipettes.pick_up_tips(
      [rack.get_item("A1"), rack.get_item("B1")], use_channels=[0, 1]
    )
    waste = prep.deck.get_resource("prep_a_waste_block")
    assert isinstance(waste, Trash)
    await prep.pipettes.drop_tips([waste, waste], use_channels=[0, 1])
    assert all(tip is None for tip in prep.pipettes.get_mounted_tips())
    assert prep.get_resource("tips") is rack
    await prep.stop()

  asyncio.run(_run())
