"""PipetteChannel facade + enumeration against the simulator."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, List
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.prep.driver.simulator import (
  SIMULATED_X_AXIS_OFFSET,
  SIMULATED_X_SPEED,
  SIMULATED_Y_DRIVE_OFFSETS,
  SIMULATED_Z_DRIVE_OFFSETS,
)
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.wire_types import HcResultEntry
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.resources.hamilton import (
  PrepDeck,
  STARLetDeck,
  hamilton_96_tiprack_10uL_NTR,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL_NTR,
  hamilton_tip_50uL,
  hamilton_tip_300uL,
)
from pylabrobot.resources.hamilton.core_gripper_tools import hamilton_core_gripper_tool
from pylabrobot.resources.tip_tracking import set_tip_tracking
from pylabrobot.resources.volume_tracker import set_volume_tracking


def _run(coro):
  asyncio.run(coro)


def _record(p: PrepSimulationDriver, only: Any = object, names: bool = False) -> List[Any]:
  """Every command `p` sends from here on (of type `only`, when given), or its name with `names`."""
  sent: List[Any] = []
  send = p.send_command

  async def record(command, *args, **kwargs):
    if isinstance(command, only):
      sent.append(type(command).__name__ if names else command)
    return await send(command, *args, **kwargs)

  p.send_command = record  # type: ignore[method-assign]
  return sent


def _index(sent: List[Any], command_type: Any) -> int:
  """Where the first command of `command_type` is in `sent`."""
  return next(i for i, c in enumerate(sent) if isinstance(c, command_type))


def _last_before(sent: List[Any], command_type: Any, index: int, dest: Any = None) -> Any:
  """The last command of `command_type` sent before `index`, to `dest` when given."""
  return next(
    c
    for c in reversed(sent[:index])
    if isinstance(c, command_type) and (dest is None or c.dest == dest)
  )


def _last_after(sent: List[Any], command_type: Any, index: int, dest: Any = None) -> Any:
  """The last command of `command_type` sent after `index`, to `dest` when given."""
  return next(
    c
    for c in reversed(sent[index + 1 :])
    if isinstance(c, command_type) and (dest is None or c.dest == dest)
  )


def test_channels_attach_the_bounds_the_device_answers():
  """Each channel carries the Y bounds the simulated device reports for it."""

  async def _t():
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup()
    assert p.pipettes is not None and p.simulated_pipettes is not None
    reported = [c.y_range for c in p.simulated_pipettes.channels]
    attached = [(ch.bounds["y_min"], ch.bounds["y_max"]) for ch in p.pipettes.channels if ch.bounds]
    assert attached == reported
    await p.stop()

  _run(_t())


def test_channels_tip_and_volume_trackers_follow_pick_up_aspirate_dispense_and_drop():
  """Tips move between their spots and the channels' shafts; wells and tips track the volume moved."""

  async def _t():
    set_tip_tracking(True)
    set_volume_tracking(True)
    try:
      deck = PrepDeck()
      tip_rack = deck[3] = hamilton_96_tiprack_50uL_NTR(name="ntr", with_tips=True)
      plate = deck[0] = cor_axy_96_wellplate_500uL_Ub("plate")
      p = PrepSimulationDriver(deck=deck)
      await p.setup()
      assert p.pipettes is not None
      spots = [tip_rack.get_item("A1"), tip_rack.get_item("B1")]
      use = [0, 1]
      src = plate["A1:B1"]
      dst = plate["A7:B7"]
      vols = [20.0] * 2
      for well in src:
        well.tracker.set_volume(100.0)

      await p.pipettes.pick_up_tips(spots, use_channels=use)
      assert all(s.tip is None for s in spots)
      for i in use:
        tip = p.pipettes.get_mounted_tip(i)
        assert tip is not None and tip.parent is p.pipettes.shaft(i)
      await p.pipettes.aspirate(
        src,
        vols=vols,
        use_channels=use,
        disable_volume_correction=[True] * 2,
      )
      for well in src:
        assert well.tracker.get_used_volume() == pytest.approx(80.0)
      for ch in use:
        tip = p.pipettes.get_mounted_tip(ch)
        assert tip is not None
        assert tip.tracker.get_used_volume() == pytest.approx(20.0)

      await p.pipettes.dispense(
        dst,
        vols=vols,
        use_channels=use,
        disable_volume_correction=[True] * 2,
      )
      for well in dst:
        assert well.tracker.get_used_volume() == pytest.approx(20.0)
      for ch in use:
        tip = p.pipettes.get_mounted_tip(ch)
        assert tip is not None
        assert tip.tracker.get_used_volume() == pytest.approx(0.0)

      await p.pipettes.drop_tips(spots, use_channels=use)
      assert all(s.tip is not None for s in spots)
      assert all(p.pipettes.get_mounted_tip(i) is None for i in use)
      await p.stop()
    finally:
      set_tip_tracking(False)
      set_volume_tracking(False)

  _run(_t())


def test_default_minimum_traverse_height_is_plrs_then_the_devices_then_the_one_setup_is_given():
  async def _t():
    p = PrepSimulationDriver(deck=STARLetDeck(), default_minimum_traverse_height=160.0)
    assert p.pipettes is not None
    assert p.pipettes.default_minimum_traverse_height == 167.5
    await p.setup()
    assert p.pipettes.default_minimum_traverse_height == 160.0
    await p.stop()
    await p.setup(default_minimum_traverse_height=150.0)
    assert p.pipettes.default_minimum_traverse_height == 150.0
    await p.stop()

  _run(_t())


def test_move_to_location_keeps_each_location_with_its_channel():
  """Locations named out of channel order still go to the channels they were named for."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_location(
      [Coordinate(150.0, 200.0, 160.0), Coordinate(150.0, 250.0, 165.0)], use_channels=[1, 0]
    )
    positions = await p.pipettes.request_locations()
    assert (positions[0].y, positions[0].z) == (250.0, 165.0)
    assert (positions[1].y, positions[1].z) == (200.0, 160.0)
    assert positions[0].x == positions[1].x == 150.0
    await p.stop()

  _run(_t())


@pytest.mark.parametrize(
  "default_x_speed, kwargs, scales",
  [
    (None, {"x_speed_scale": 25, "z_speed": 40.0}, [25, 100]),
    (None, {"x_speed": 150.0}, [25, 100]),
    (None, {}, [53, 100]),
    (60.0, {}, [10, 100]),
  ],
)
def test_move_to_location_sets_an_x_speed_scale_for_the_move_and_puts_it_back(
  default_x_speed, kwargs, scales
):
  """x_speed_scale is sent as given, x_speed in mm/s as the nearest scale, and with neither the
  default_x_speed; the descent takes z_speed in mm/s."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    if default_x_speed is not None:
      p.pipettes.default_x_speed = default_x_speed
    sent = _record(p)
    await p.pipettes.move_to_location(Coordinate(150.0, 360.0, 160.0), use_channels=0, **kwargs)
    move = _index(sent, PrepCmd.PrepMoveToPosition)
    assert [c.value for c in sent if isinstance(c, PrepCmd.PrepSetXSpeedScale)] == scales
    assert _last_before(sent, PrepCmd.PrepSetXSpeedScale, move).value == scales[0]
    descent = _last_after(sent, PrepCmd.PrepMoveZAbsolute, move)
    assert descent.velocity == kwargs.get("z_speed", p.pipettes.default_z_speed)
    await p.stop()

  _run(_t())


@pytest.mark.parametrize(
  "kwargs, refusal",
  [
    ({"x_speed_scale": 0}, "between 1 and 100"),
    ({"x_speed": 150.0, "x_speed_scale": 25}, "not both"),
    ({"x_speed": 500.0}, "between 6.0 and 400.0 mm/s"),
  ],
)
def test_move_to_location_refuses_an_x_speed_it_cannot_send(kwargs, refusal):
  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    with pytest.raises(ValueError, match=refusal):
      await p.pipettes.move_to_location(Coordinate(150.0, 360.0, 160.0), **kwargs)
    await p.stop()

  _run(_t())


def test_y_and_z_moves_send_every_channel_at_the_default_speed():
  """One-axis moves carry each channel's device ChannelIndex, the channels not named where they stand, and the
  default speed."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    before = await p.pipettes.request_locations()
    sent = _record(p)
    await p.pipettes.move_to_y_position(0, 370.0)
    await p.pipettes.move_tool_bottom_to_z_position(1, 150.0)
    y_move = next(c for c in sent if isinstance(c, PrepCmd.PrepMoveYAbsolute))
    z_move = next(c for c in sent if isinstance(c, PrepCmd.PrepMoveZAbsolute))
    rear, front = p.pipettes.channel_enum(0), p.pipettes.channel_enum(1)
    assert [(int(c.channel), c.y_position) for c in y_move.channels] == [
      (rear, 370.0),
      (front, before[1].y),
    ]
    assert y_move.velocity == p.pipettes.default_y_speed
    assert [(int(c.channel), c.z_position) for c in z_move.channels] == [
      (rear, before[0].z),
      (front, 150.0),
    ]
    assert z_move.velocity == p.pipettes.default_z_speed
    after = await p.pipettes.request_locations()
    assert (after[0].x, after[0].y, after[1].z) == (before[0].x, 370.0, 150.0)

    # Positions named out of channel order still go to the channels they were named for.
    await p.pipettes.move_to_y_positions({1: 75.0, 0: 100.0})
    await p.pipettes.move_tool_bottom_to_z_positions({1: 75.0, 0: 50.0})
    after = await p.pipettes.request_locations()
    assert [(at.y, at.z) for at in after] == [(100.0, 50.0), (75.0, 75.0)]
    with pytest.raises(ValueError, match="no channel 2 on this Prep"):
      await p.pipettes.move_to_y_positions({2: 50.0})
    with pytest.raises(ValueError, match="out of range"):
      await p.pipettes.move_tool_bottom_to_z_positions({2: 50.0})
    await p.stop()

  _run(_t())


def test_y_and_z_moves_send_the_speed_they_are_given():
  """A speed named on any of the four Y and Z moves is the velocity sent; one not above 0 is refused unsent."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent = _record(p)
    await p.pipettes.move_to_y_positions({0: 370.0, 1: 350.0}, speed=50.0)
    await p.pipettes.move_to_y_position(1, 340.0, speed=60.0)
    await p.pipettes.move_tool_bottom_to_z_positions({0: 150.0, 1: 140.0}, speed=30.0)
    await p.pipettes.move_tool_bottom_to_z_position(0, 160.0, speed=40.0)
    moves = [
      c for c in sent if isinstance(c, (PrepCmd.PrepMoveYAbsolute, PrepCmd.PrepMoveZAbsolute))
    ]
    assert [(type(c).__name__, c.velocity) for c in moves] == [
      ("PrepMoveYAbsolute", 50.0),
      ("PrepMoveYAbsolute", 60.0),
      ("PrepMoveZAbsolute", 30.0),
      ("PrepMoveZAbsolute", 40.0),
    ]
    sent.clear()
    for refused in (
      lambda: p.pipettes.move_to_y_positions({0: 370.0}, speed=0),
      lambda: p.pipettes.move_to_y_position(0, 370.0, speed=-5.0),
    ):
      with pytest.raises(ValueError, match="speed must be above 0 mm/s"):
        await refused()
    # Z: within the speeds the drive has been sent at and runs.
    for refused in (
      lambda: p.pipettes.move_tool_bottom_to_z_positions({0: 150.0}, speed=0),
      lambda: p.pipettes.move_tool_bottom_to_z_position(0, 150.0, speed=9.9),
      lambda: p.pipettes.move_tool_bottom_to_z_position(0, 150.0, speed=142.1),
    ):
      with pytest.raises(ValueError, match="speed must be between 10.0 and 142.0 mm/s"):
        await refused()
    assert sent == []
    await p.stop()

  _run(_t())


def test_z_moves_hold_the_acceleration_for_the_move_and_put_it_back():
  """Each Z drive moves at the given acceleration and is restored after, even when the move fails.

  With none given nothing is set; one not above 0 is refused unsent.
  """

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    drives = (await p.request_channel_drives()).zdrive_addrs
    fail_moves = False
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      if fail_moves and isinstance(command, PrepCmd.PrepMoveZAbsolute):
        raise RuntimeError("stuck")
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_tool_bottom_to_z_positions({0: 150.0, 1: 140.0}, acceleration=400.0)
    move = _index(sent, PrepCmd.PrepMoveZAbsolute)
    for drive in drives:
      assert _last_before(sent, PrepCmd.PrepZDriveSetAcceleration, move, drive).value == 400.0
      assert _last_after(sent, PrepCmd.PrepZDriveSetAcceleration, move, drive).value == 800.0

    sent.clear()
    await p.pipettes.move_tool_bottom_to_z_position(0, 160.0)
    assert not any(isinstance(c, PrepCmd.PrepZDriveSetAcceleration) for c in sent)

    sent.clear()
    fail_moves = True
    with pytest.raises(RuntimeError, match="stuck"):
      await p.pipettes.move_tool_bottom_to_z_position(1, 150.0, acceleration=300.0)
    fail_moves = False
    move = _index(sent, PrepCmd.PrepMoveZAbsolute)
    for drive in drives:
      assert _last_before(sent, PrepCmd.PrepZDriveSetAcceleration, move, drive).value == 300.0
      assert _last_after(sent, PrepCmd.PrepZDriveSetAcceleration, move, drive).value == 800.0

    sent.clear()
    for refused in (
      lambda: p.pipettes.move_tool_bottom_to_z_positions({0: 150.0}, acceleration=0),
      lambda: p.pipettes.move_tool_bottom_to_z_position(0, 150.0, acceleration=-100.0),
    ):
      with pytest.raises(ValueError, match="acceleration must be above 0 mm/s2"):
        await refused()
    assert not any(
      isinstance(c, (PrepCmd.PrepZDriveSetAcceleration, PrepCmd.PrepMoveZAbsolute)) for c in sent
    )
    await p.stop()

  _run(_t())


def test_probe_y_using_clld_searches_with_the_channels_own_y_axis():
  """The channel's Y axis is sent the end of the search in its drive frame; nothing detected is None."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    assert [(c.yaxis, c.ydrive) for c in p.pipettes.channels] == [
      (Address(1, node, 259), Address(1, node, 515)) for node in (236, 238)
    ]
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 200.0})
    sent = _record(p)
    found = await p.pipettes.probe_y_using_clld(
      1, "forward", search_end_position=150.0, search_speed=5.0, allow_without_tip=True
    )
    assert found is None
    seek = next(c for c in sent if isinstance(c, PrepCmd.PrepYAxisSeekCapacitiveLld))
    assert seek.dest == p.pipettes.channels[1].yaxis
    assert seek.position == pytest.approx(150.0 + SIMULATED_Y_DRIVE_OFFSETS[1])
    assert (seek.velocity, seek.detect_mode, seek.sensitivity) == (
      5.0,
      2,
      p.pipettes.default_clld_sensitivity,
    )
    assert (await p.pipettes.request_locations())[1].y == pytest.approx(150.0)
    await p.stop()

  _run(_t())


def test_probe_y_using_clld_refuses_searches_it_cannot_make():
  """Out of range, into a neighbour, backwards, or without speed: refused before anything is sent."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 200.0})
    sent = _record(p, names=True)
    probe = functools.partial(p.pipettes.probe_y_using_clld, allow_without_tip=True)
    for call, message in (
      (
        lambda: probe(1, "backward", search_end_position=380.0),  # past its own window
        "outside the range channel 1 may reach",
      ),
      (lambda: probe(1, "backward", search_end_position=150.0), "cannot end at"),
      (lambda: probe(1, "sideways"), "direction"),  # type: ignore[arg-type]
      (lambda: probe(1, "forward", search_speed=0), "search_speed must be above 0"),
      (lambda: probe(2, "forward"), "channel_idx must be between"),
    ):
      with pytest.raises(ValueError, match=message):
        await call()
    assert not {"PrepYAxisSeekCapacitiveLld", "PrepMoveYAbsolute"} & set(sent)
    await p.stop()

  _run(_t())


def test_probe_x_using_clld_steps_the_arm_with_detection_on_and_puts_everything_back():
  """The arm steps 0.1 mm at a time at the probe speed with detection on; speed restored after."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    channel = p.pipettes.channels[1]
    assert (channel.calibration, channel.clld) == (Address(1, 238, 512), Address(1, 238, 261))
    here = (await p.pipettes.request_locations())[1].x
    sent = _record(p)
    # The device's search, not the simulator's lookup.
    p.pipettes._search_x_using_clld = functools.partial(  # type: ignore[method-assign]
      Pipettes._search_x_using_clld, p.pipettes
    )
    found = await p.pipettes.probe_x_using_clld(
      1, "left", search_end_position=here - 0.5, allow_without_tip=True
    )
    assert found is None
    steps = [i for i, c in enumerate(sent) if isinstance(c, PrepCmd.PrepXAxisMoveAbsolute)]
    start = _index(sent, PrepCmd.PrepChannelStartCLldDetection)
    stop = _index(sent, PrepCmd.PrepChannelStopCLldDetection)
    assert start < steps[0] and steps[-1] < stop
    assert [sent[i].position for i in steps] == pytest.approx(
      [here - 0.1 * i - SIMULATED_X_AXIS_OFFSET for i in range(1, 6)]
    )
    assert (sent[start].dest, sent[start].detect_mode, sent[start].sensitivity) == (
      channel.calibration,
      2,
      p.pipettes.default_clld_sensitivity,
    )
    assert sent[stop].dest == channel.calibration
    assert _last_before(sent, PrepCmd.PrepXAxisSetVelocity, steps[0]).value == 5.0
    assert _last_after(sent, PrepCmd.PrepXAxisSetVelocity, steps[-1]).value == SIMULATED_X_SPEED
    assert await p.x_arm.request_position() == pytest.approx(here - 0.5)
    await p.stop()

  _run(_t())


def test_probe_x_using_clld_refuses_searches_it_cannot_make():
  """Unknown direction, no speed, backwards, or no such channel: refused before anything moves."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    here = (await p.pipettes.request_locations())[1].x
    sent = _record(p, names=True)
    probe = functools.partial(p.pipettes.probe_x_using_clld, allow_without_tip=True)
    for call, message in (
      (lambda: probe(1, "up"), "direction"),  # type: ignore[arg-type]
      (lambda: probe(1, "left", search_end_position=here + 1.0), "cannot end at"),
      (lambda: probe(2, "left"), "channel_idx must be between"),
    ):
      with pytest.raises(ValueError, match=message):
        await call()
    assert not {
      "PrepChannelStartCLldDetection",
      "PrepXAxisMoveAbsolute",
      "PrepXAxisSetVelocity",
    } & set(sent)
    await p.stop()

  _run(_t())


def _deck_with_block(x: float, y: float, size_x: float = 10.0, size_y: float = 10.0) -> PrepDeck:
  """A Prep deck with a 60 mm tall block at (x, y, 0)."""
  deck = PrepDeck()
  deck.assign_child_resource(
    Resource("block", size_x=size_x, size_y=size_y, size_z=60.0), location=Coordinate(x, y, 0.0)
  )
  return deck


def test_simulated_y_probe_stops_at_the_first_resource_in_the_way():
  """The Y probe stops where the lowered channel meets a resource, and returns its face."""

  async def _t():
    p = PrepSimulationDriver(deck=_deck_with_block(285.0, 290.0))
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 340.0, 1: 320.0})
    await p.pipettes.move_tool_bottom_to_z_positions({1: 50.0})
    surface = await p.pipettes.probe_y_using_clld(
      1, "forward", search_end_position=290.0, allow_without_tip=True
    )
    assert surface == pytest.approx(300.0)
    assert (await p.pipettes.request_locations())[1].y == pytest.approx(305.5)
    # Raised above the block, the channel passes over it. The deck's own waste block is 75 mm
    # tall, so clearing it takes more than the test block's 60.
    await p.pipettes.move_tool_bottom_to_z_positions({1: 80.0})
    await p.pipettes.move_to_y_positions({1: 320.0})
    found = await p.pipettes.probe_y_using_clld(
      1, "forward", search_end_position=290.0, allow_without_tip=True
    )
    assert found is None
    # With a tip mounted, the surface is half the tip bottom beyond where the channel met the block.
    presence = AsyncMock(return_value=[False, True])
    p.pipettes.sense_tip_presence = presence  # type: ignore[method-assign]
    await p.pipettes.move_tool_bottom_to_z_positions({1: 50.0})
    await p.pipettes.move_to_y_positions({1: 320.0})
    surface = await p.pipettes.probe_y_using_clld(1, "forward", search_end_position=290.0)
    assert surface == pytest.approx(303.5 - 0.6)
    await p.stop()

  _run(_t())


def test_simulated_y_probe_backward_uses_the_stop_disc_diameter_it_is_given():
  """Probing backward, the surface is half the stop disc beyond where the channel met it."""

  async def _t():
    p = PrepSimulationDriver(deck=_deck_with_block(285.0, 350.0))
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 320.0, 1: 300.0})
    await p.pipettes.move_tool_bottom_to_z_positions({0: 50.0})
    surface = await p.pipettes.probe_y_using_clld(
      0, "backward", search_end_position=370.0, allow_without_tip=True
    )
    assert surface == pytest.approx(350.0)
    assert (await p.pipettes.request_locations())[0].y == pytest.approx(346.5 - 2.0)
    await p.pipettes.move_to_y_positions({0: 320.0})
    surface = await p.pipettes.probe_y_using_clld(
      0, "backward", search_end_position=370.0, stop_disc_diameter=2.0, allow_without_tip=True
    )
    assert surface == pytest.approx(346.5 + 1.0)
    await p.stop()

  _run(_t())


def test_simulated_x_probe_stops_the_arm_at_the_first_resource_in_the_way():
  """The X probe looks the search up in the model and moves the arm once, to the resource's face."""

  async def _t():
    p = PrepSimulationDriver(deck=_deck_with_block(270.0, 295.0, size_x=14.0))
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    await p.pipettes.move_to_y_positions({0: 340.0, 1: 300.0})
    await p.pipettes.move_tool_bottom_to_z_positions({1: 50.0})
    sent = _record(p)
    surface = await p.pipettes.probe_x_using_clld(
      1, "left", search_end_position=275.0, allow_without_tip=True
    )
    assert surface == pytest.approx(284.0)
    # One move to where the channel meets the block, and the back-off; in the axis frame.
    moves = [c.position for c in sent if isinstance(c, PrepCmd.PrepXAxisMoveAbsolute)]
    assert moves == pytest.approx(
      [287.5 - SIMULATED_X_AXIS_OFFSET, 289.5 - SIMULATED_X_AXIS_OFFSET]
    )
    assert not any(isinstance(c, PrepCmd.PrepCLldGetStatus) for c in sent)
    assert await p.x_arm.request_position() == pytest.approx(287.5 + 2.0)
    await p.stop()

  _run(_t())


@pytest.mark.parametrize("probe", ["probe_z_using_clld", "probe_z_using_ztouch"])
def test_simulated_z_probe_detects_the_top_of_a_resource_below(probe):
  """A Z probe over a resource detects its top; the channel is left 2 mm above it."""

  async def _t():
    p = PrepSimulationDriver(deck=_deck_with_block(285.0, 295.0))
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 340.0, 1: 300.0})
    found = await getattr(p.pipettes, probe)(
      1, search_start_position=160.0, search_end_position=20.0, allow_without_tip=True
    )
    assert found == pytest.approx(60.0)
    assert (await p.pipettes.request_locations())[1].z == pytest.approx(62.0)
    await p.stop()

  _run(_t())


def test_probe_z_using_clld_leaves_the_channel_where_it_detected():
  """With no post-detection distance the seek is the only move, and it ends at the surface."""

  async def _t():
    p = PrepSimulationDriver(deck=_deck_with_block(285.0, 295.0))
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 340.0, 1: 300.0})
    sent = _record(p)
    found = await p.pipettes.probe_z_using_clld(
      1,
      search_start_position=160.0,
      search_end_position=20.0,
      allow_without_tip=True,
      post_detection_distance=0,
    )
    assert found == pytest.approx(60.0)
    assert (await p.pipettes.request_locations())[1].z == pytest.approx(60.0)
    assert not {"PrepMoveZAbsolute", "PrepMoveToPosition"} & {type(c).__name__ for c in sent}
    await p.stop()

  _run(_t())


def test_probe_z_using_ztouch_seeks_with_the_channels_own_z_axis():
  """The channel's Z axis is sent start, floor and final height in its drive frame; nothing met is None."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    assert [(c.zaxis, c.zdrive) for c in p.pipettes.channels] == [
      (Address(1, node, 260), Address(1, node, 516)) for node in (236, 238)
    ]
    await p.x_arm.move_to_x_position(100.0)
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 100.0})
    sent = _record(p)
    found = await p.pipettes.probe_z_using_ztouch(
      1,
      search_start_position=160.0,
      search_speed=5.0,
      search_end_position=100.0,
      allow_without_tip=True,
    )
    assert found is None
    seek = next(c for c in sent if isinstance(c, PrepCmd.PrepZAxisSeekObstacle))
    offset = SIMULATED_Z_DRIVE_OFFSETS[1]
    assert seek.dest == p.pipettes.channels[1].zaxis
    assert (seek.start_position, seek.end_position, seek.final_position, seek.velocity) == (
      pytest.approx((160.0 + offset, 100.0 + offset, 160.0 + offset, 5.0))
    )
    assert (await p.pipettes.request_locations())[1].z == pytest.approx(160.0)
    await p.stop()

  _run(_t())


def test_probe_z_using_ztouch_seeks_the_tip_bottom_and_can_end_at_z_safety():
  """Heights are of the tip bottom, by the held tip's length or `tip_len`; all channels to Z safety after."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    await p.x_arm.move_to_x_position(100.0)
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 100.0})
    shaft = p.pipettes.shaft(1)
    assert shaft is not None
    shaft.mount_tip(hamilton_tip_300uL(name="tip"))
    assert await p.pipettes.request_held_tip_length() == pytest.approx(51.9)
    here = (await p.pipettes.request_locations())[1].z
    drive = await p.pipettes.channels[1].request_z_drive_position()
    sent = _record(p)
    probe = functools.partial(
      p.pipettes.probe_z_using_ztouch,
      1,
      search_start_position=100.0,
      search_end_position=70.0,  # above the spot below it, so the seek touches nothing
    )
    assert await probe() is None
    assert await probe(tip_len=70.0, move_channels_to_safe_pos_after=True) is None
    seeks = [c for c in sent if isinstance(c, PrepCmd.PrepZAxisSeekObstacle)]
    stop_disc_offset = drive - (here + 51.9)
    assert seeks[0].start_position == pytest.approx(100.0 + 51.9 + stop_disc_offset)
    assert seeks[1].start_position == pytest.approx(100.0 + 62.0 + stop_disc_offset)
    assert _index(sent[sent.index(seeks[1]) :], PrepCmd.PrepMoveZUpToSafe) > 0
    with pytest.raises(ValueError, match="tip_len must be between 20 and 120"):
      await probe(tip_len=10.0)
    await p.stop()

  _run(_t())


def test_probe_z_using_ztouch_holds_the_push_force_for_the_seek_and_puts_it_back():
  """push_force_pwm is set before the seek and restored after; below 40 the drive cannot lift, so it is refused."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    await p.x_arm.move_to_x_position(100.0)
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 100.0})
    sent = _record(p)
    await p.pipettes.probe_z_using_ztouch(
      1,
      search_start_position=160.0,
      search_end_position=100.0,
      allow_without_tip=True,
      push_force_pwm=40,
    )
    seek = _index(sent, PrepCmd.PrepZAxisSeekObstacle)
    held = _last_before(sent, PrepCmd.PrepZDriveSetPwm, seek)
    put_back = _last_after(sent, PrepCmd.PrepZDriveSetPwm, seek)
    assert (held.value, held.dest) == (40, p.pipettes.channels[1].zdrive)
    assert put_back.value == 125
    with pytest.raises(ValueError, match="push_force_pwm must be between 40 and 125"):
      await p.pipettes.probe_z_using_ztouch(1, allow_without_tip=True, push_force_pwm=30)
    await p.stop()

  _run(_t())


def test_probe_z_using_ztouch_reads_an_untouched_search_as_nothing():
  """The firmware answers the end of an untouched search as a detection; that comes back as None."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    await p.x_arm.move_to_x_position(100.0)
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 100.0})
    offset = SIMULATED_Z_DRIVE_OFFSETS[1]
    at_the_end = PrepCmd.PrepZAxisSeekObstacle.Response(
      obstacle_detected=True, position=100.0 - 0.9 + offset
    )
    p.pipettes._unchecked_fw_z_axis_seek_obstacle = AsyncMock(return_value=at_the_end)  # type: ignore[method-assign]
    found = await p.pipettes.probe_z_using_ztouch(
      1, search_start_position=160.0, search_end_position=100.0, allow_without_tip=True
    )
    assert found is None
    # A surface well above the end is a real detection.
    at_a_surface = PrepCmd.PrepZAxisSeekObstacle.Response(
      obstacle_detected=True, position=120.0 + offset
    )
    p.pipettes._unchecked_fw_z_axis_seek_obstacle = AsyncMock(return_value=at_a_surface)  # type: ignore[method-assign]
    found = await p.pipettes.probe_z_using_ztouch(
      1, search_start_position=160.0, search_end_position=100.0, allow_without_tip=True
    )
    assert found == pytest.approx(120.0)
    await p.stop()

  _run(_t())


def test_probe_z_using_ztouch_refuses_seeks_it_cannot_make():
  """No speed, the floor above the start, or outside the Z range: refused before anything is sent."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent = _record(p, names=True)
    probe = functools.partial(p.pipettes.probe_z_using_ztouch, 1, allow_without_tip=True)
    for kwargs, message in (
      ({"search_speed": 0}, "search_speed must be above 0"),
      ({"search_start_position": 100.0, "search_end_position": 120.0}, "must be below"),
      ({"search_end_position": 5.0}, "outside channel 1 range"),
    ):
      with pytest.raises(ValueError, match=message):
        await probe(**kwargs)
    with pytest.raises(ValueError, match="channel_idx must be between"):
      await p.pipettes.probe_z_using_ztouch(2, allow_without_tip=True)
    assert "PrepZAxisSeekObstacle" not in sent
    await p.stop()

  _run(_t())


def test_probes_check_the_tip_before_anything_else():
  """Without allow_without_tip, a probe needs a tip and refuses before moving otherwise."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent = _record(p, names=True)
    with pytest.raises(RuntimeError, match="no tip on channel 1"):
      await p.pipettes.probe_y_using_clld(1, "forward", search_end_position=150.0)
    with pytest.raises(RuntimeError, match="no tip on channel 1"):
      await p.pipettes.probe_x_using_clld(1, "left")
    with pytest.raises(RuntimeError, match="no tip on channel 1"):
      await p.pipettes.probe_z_using_clld(1, search_start_position=160.0, search_end_position=100.0)
    with pytest.raises(RuntimeError, match="no tip on channel 1"):
      await p.pipettes.probe_z_using_ztouch(
        1, search_start_position=160.0, search_end_position=100.0
      )
    assert not {
      "PrepYAxisSeekCapacitiveLld",
      "PrepXAxisMoveAbsolute",
      "PrepZSeekLldPosition",
      "PrepZAxisSeekObstacle",
      "PrepMoveYAbsolute",
    } & set(sent)
    await p.stop()

  _run(_t())


def test_probe_z_using_clld_seeks_where_the_channel_stands():
  """The seek is sent where the channel stands, every argument written out; a miss is raised back."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    before = (await p.pipettes.request_locations())[1]
    sent = _record(p)
    found = await p.pipettes.probe_z_using_clld(
      1, search_start_position=160.0, search_end_position=100.0, allow_without_tip=True
    )
    assert found is None  # nothing on the deck under the channel
    seeks = [c for c in sent if type(c).__name__ == "PrepZSeekLldPosition"]
    assert len(seeks) == 1
    seek = seeks[0].seek_parameters[0]
    assert int(seek.channel) == int(PrepCmd.ChannelIndex.FrontChannel)
    assert (seek.seek_position_x, seek.seek_position_y) == pytest.approx(
      (before.x, before.y), abs=1e-3
    )
    assert (seek.seek_height, seek.min_seek_height, seek.final_position_z) == (160.0, 100.0, 100.0)
    assert (seek.seek_velocity_z, seek.lld_sensitivity, seek.detect_mode) == (10.0, 3, 0)
    after = (await p.pipettes.request_locations())[1]
    assert (after.x, after.y, after.z) == pytest.approx((before.x, before.y, 160.0), abs=1e-3)
    await p.stop()

  _run(_t())


def test_probe_z_using_clld_refuses_before_sending():
  """A floor above the start, a height out of reach or an unknown channel is refused."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    p.pipettes.configuration.channels[0].z_range = (18.0, 167.5)
    with pytest.raises(ValueError, match="above search_start_position"):
      await p.pipettes.probe_z_using_clld(
        0, search_start_position=120.0, search_end_position=130.0, allow_without_tip=True
      )
    with pytest.raises(ValueError, match="search_end_position=10.0 outside channel 0"):
      await p.pipettes.probe_z_using_clld(
        0, search_start_position=160.0, search_end_position=10.0, allow_without_tip=True
      )
    with pytest.raises(ValueError, match="channel_idx must be between"):
      await p.pipettes.probe_z_using_clld(
        5, search_start_position=160.0, search_end_position=100.0, allow_without_tip=True
      )
    await p.stop()

  _run(_t())


def test_x_arm_move_sets_speed_and_acceleration_for_the_axis_move_and_puts_them_back():
  """The X axis move runs at the given (or default) speed and acceleration, restored afterwards."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    await p.pipettes.move_to_safe_z()
    sent = _record(p)
    await p.x_arm.move_to_x_position(200.0)
    move = _index(sent, PrepCmd.PrepXAxisMoveAbsolute)
    assert sent[move].position == pytest.approx(200.0 - SIMULATED_X_AXIS_OFFSET, abs=1e-3)
    assert _last_before(sent, PrepCmd.PrepXAxisSetVelocity, move).value == 320.0
    assert _last_before(sent, PrepCmd.PrepXAxisSetAcceleration, move).value == 1800.0
    assert _last_after(sent, PrepCmd.PrepXAxisSetVelocity, move).value == 400.0
    assert _last_after(sent, PrepCmd.PrepXAxisSetAcceleration, move).value == 2250.0
    assert await p.x_arm.request_position() == 200.0

    sent.clear()
    await p.x_arm.move_to_x_position(150.0, speed=100.0, acceleration=500.0)
    move = _index(sent, PrepCmd.PrepXAxisMoveAbsolute)
    assert _last_before(sent, PrepCmd.PrepXAxisSetVelocity, move).value == 100.0
    assert _last_before(sent, PrepCmd.PrepXAxisSetAcceleration, move).value == 500.0
    await p.stop()

  _run(_t())


def test_x_arm_move_raises_lowered_channels_up_to_the_height_it_is_given():
  """The arm travels at the traverse height; a height of 0 raises nothing."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    traverse = p.pipettes.default_minimum_traverse_height

    await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 100.0), use_channels=1)
    await p.x_arm.move_to_x_position(200.0)
    assert await p.x_arm.request_position() == 200.0
    assert [at.z for at in await p.pipettes.request_locations()] == [traverse, traverse]

    await p.pipettes.move_to_location(Coordinate(200.0, 200.0, 100.0), use_channels=1)
    z_before = [at.z for at in await p.pipettes.request_locations()]
    await p.x_arm.move_to_x_position(180.0, minimum_traverse_height_start=0)
    assert [at.z for at in await p.pipettes.request_locations()] == z_before

    with pytest.raises(ValueError, match="speed must be above 0"):
      await p.x_arm.move_to_x_position(200.0, speed=500.0)
    await p.stop()

  _run(_t())


def test_x_arm_move_raises_a_channel_only_as_high_as_what_it_carries_allows():
  """A channel carrying something travels at its own ceiling, not at the traverse height."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    traverse = p.pipettes.default_minimum_traverse_height
    # What the device answers for a channel holding the 51.9 mm teaching needle.
    p.pipettes.configuration.channels[1].z_range = (-33.9, 115.6)

    await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 100.0), use_channels=1)
    await p.x_arm.move_to_x_position(200.0)
    assert [at.z for at in await p.pipettes.request_locations()] == [traverse, 115.6]
    await p.stop()

  _run(_t())


def test_x_arm_probe_home_flag_seeks_at_the_given_speed_and_returns_the_deck_frame():
  """The seek runs at the probe speed with its arguments written out; speed restored after."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    await p.pipettes.move_to_safe_z()
    start = await p.x_arm.request_position()
    assert start is not None
    sent = _record(p)
    tripped = await p.x_arm.probe_home_flag(-30.0)
    assert tripped == pytest.approx(start, abs=1e-3)  # no flag is modelled in simulation
    seek = _index(sent, PrepCmd.PrepXAxisSeekToHomeFlag)
    assert (sent[seek].distance, sent[seek].travel_limits_enable, sent[seek].trip_sense) == (
      -30.0,
      True,
      0,
    )
    assert _last_before(sent, PrepCmd.PrepXAxisSetVelocity, seek).value == 100.0
    assert _last_after(sent, PrepCmd.PrepXAxisSetVelocity, seek).value == 400.0

    p.pipettes.configuration.channels[0].x_range = (0.0, 299.0)
    with pytest.raises(ValueError, match="outside the channels' range"):
      await p.x_arm.probe_home_flag(-1000.0)
    await p.stop()

  _run(_t())


def test_probe_z_using_clld_defaults_lowest_z_to_the_bottom_of_the_channel_z_range():
  """With no floor given, the seek goes down to the bottom of the channel's Z range."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    p.pipettes.configuration.channels[1].z_range = (18.03, 167.5)
    sent = _record(p)
    await p.pipettes.probe_z_using_clld(1, search_start_position=160.0, allow_without_tip=True)
    seek = next(c for c in sent if type(c).__name__ == "PrepZSeekLldPosition").seek_parameters[0]
    assert seek.min_seek_height == pytest.approx(18.03)

    p.pipettes.configuration.channels[1].z_range = None
    with pytest.raises(RuntimeError, match="Z range has not been read"):
      await p.pipettes.probe_z_using_clld(1, search_start_position=160.0, allow_without_tip=True)
    await p.stop()

  _run(_t())


def test_channel_order_comes_from_the_device():
  """Channels are ordered back to front by how far back they reach; the legacy order is the fallback."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    rear, front = int(PrepCmd.ChannelIndex.RearChannel), int(PrepCmd.ChannelIndex.FrontChannel)
    assert p.pipettes.channel_order == (rear, front)
    assert p.pipettes.channel_of(front) == 1
    assert p.pipettes.channel_of(int(PrepCmd.ChannelIndex.MPHChannel)) is None
    await p.stop()

  _run(_t())

  def bounds(channel, y_max):
    return PrepCmd.ChannelBoundsParameters(channel, 0.0, 300.0, y_max - 385.0, y_max, 18.0, 167.5)

  present = [
    PrepCmd.ChannelIndex.FrontChannel,
    PrepCmd.ChannelIndex.RearChannel,
    PrepCmd.ChannelIndex.MPHChannel,
  ]
  order = Pipettes._order_channels(present, [bounds(1, 376.0), bounds(2, 385.0)])
  assert order == (2, 1)
  assert Pipettes._order_channels(None, []) == (2, 1)
  assert Pipettes._order_channels([PrepCmd.ChannelIndex.FrontChannel], []) == (1,)


def test_minimum_y_spacing_comes_from_the_channel_windows():
  """The offset between neighbouring channels' Y windows is the spacing kept between them."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    assert p.pipettes._min_spacing_between(0, 1) == 9.0
    p.pipettes.configuration.channels[0].y_range = (0.0, 380.0)
    with pytest.raises(RuntimeError, match="spacing is unknown"):
      p.pipettes._min_spacing_between(0, 1)
    await p.stop()

  _run(_t())


def test_spacing_refusals_say_what_is_wrong_and_what_would_fit():
  """Out of order and too close read differently, give both positions and where either channel could go, and
  name a channel that was not asked to move - pointing to make_space where the move offers it."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 200.0, 1: 91.0})
    here = await p.pipettes.request_locations()
    with pytest.raises(ValueError) as refused:
      await p.pipettes.move_to_y_positions({0: 80.0})
    assert str(refused.value) == (
      "Channel 0 would be 11.00 mm in front of channel 1 (y=80.00 and y=91.00 mm). Channels are numbered from "
      "the back, so channel 0 needs the larger y; they must be at least 9.0 mm apart. Send channel 0 to "
      "y >= 100.00 or channel 1 to y <= 71.00. Channel 1 was not named, so it stays at y=91.00 mm; "
      "make_space=True moves it out of the way."
    )
    with pytest.raises(ValueError) as refused:
      await p.pipettes.move_to_y_positions({0: 100.0, 1: 95.0})
    assert str(refused.value) == (
      "Channels 0 and 1 would be 5.00 mm apart (y=100.00 and y=95.00 mm); they must be at least 9.0 mm apart. "
      "Send channel 0 to y >= 104.00 or channel 1 to y <= 91.00."
    )
    with pytest.raises(
      ValueError,
      match=r"Channel 0 was not named, so it stays at y=200\.00 mm; make_space=True moves it out of "
      r"the way\.$",
    ):
      await p.pipettes.move_to_location(Coordinate(here[1].x, 195.0, here[1].z), use_channels=1)
    assert await p.pipettes.request_locations() == here  # every refusal came before a move

    # And with it, channel 0 gives way rather than the move being refused.
    await p.pipettes.move_to_location(
      Coordinate(here[1].x, 195.0, here[1].z), use_channels=1, make_space=True
    )
    after = await p.pipettes.request_locations()
    assert round(after[1].y, 2) == 195.0
    assert round(after[0].y, 2) == 204.0  # pushed back to the minimum spacing, and no further
    await p.stop()

  _run(_t())


def test_move_to_y_positions_make_space_moves_the_channel_not_named():
  """With make_space, a channel in the way is pushed just far enough, in the same single move."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    before = await p.pipettes.request_locations()
    target = before[0].y - 5.0
    with pytest.raises(ValueError, match="at least 9.0 mm apart"):
      await p.pipettes.move_to_y_positions({1: target})
    sent = _record(p, names=True)
    await p.pipettes.move_to_y_positions({1: target}, make_space=True)
    assert sent.count("PrepMoveYAbsolute") == 1
    after = await p.pipettes.request_locations()
    assert after[1].y == pytest.approx(target)
    assert after[0].y == pytest.approx(target + 9.0)
    await p.stop()

  _run(_t())


def test_a_channel_without_reported_bounds_keeps_the_default_y_window():
  """Setup replaces each channel's default Y window with the one its device reports, and keeps the default
  where the device reports none."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    assert p.simulated_pipettes is not None
    p.simulated_pipettes.channels[
      0
    ].y_range = None  # the simulated device reports no bounds for the rear channel
    p.simulated_pipettes.channels[1].y_range = (-8.0, 375.0)
    await p.setup()
    assert p.pipettes is not None
    c = p.pipettes.configuration
    assert c.channels[0].y_range == c.default_y_ranges[0] == (0.0, 385.0)
    assert c.channels[1].y_range == (-8.0, 375.0)
    await p.stop()

  _run(_t())

  fresh = Pipettes.__init__.__globals__["PipettesConfiguration"]()
  fresh.resolve_channels(2)
  assert [ch.y_range for ch in fresh.channels] == [(0.0, 385.0), (-9.0, 376.0)]


def test_default_y_windows_are_not_applied_on_a_device_with_an_8_channel_head():
  """The 8-channel head rides the channels' Y rail, so a channel it reports no bounds for gets no default window."""
  from pylabrobot.hamilton.prep.driver.simulator import RECORDING_PREP_HEAD8

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck(), declared_configuration_json=RECORDING_PREP_HEAD8)
    assert p.simulated_pipettes is not None
    p.simulated_pipettes.channels[
      0
    ].y_range = None  # the simulated device reports no bounds for the rear channel
    await p.setup()
    assert p.pipettes is not None and p.head8 is not None
    assert p.pipettes.configuration.channels[0].y_range is None
    assert p.pipettes.configuration.channels[1].y_range is not None
    await p.stop()

  _run(_t())


def test_move_to_xy_positions_raises_every_low_channel_then_travels_at_that_height():
  """A channel left out of the move still rides the gantry, so it is raised with the others."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_location(
      [Coordinate(150.0, 250.0, 90.0), Coordinate(150.0, 200.0, 80.0)], use_channels=[0, 1]
    )
    sent = _record(p, names=True)
    await p.pipettes.move_to_xy_positions(200.0, {0: 300.0}, minimum_traverse_height_start=160.0)

    # up first, then across: the gantry never has a descent to blend into the lateral move
    assert sent.index("PrepMoveZAbsolute") < sent.index("PrepMoveToPosition")
    assert sent.count("PrepMoveToPosition") == 1
    at = await p.pipettes.request_locations()
    assert (at[0].x, at[0].y, at[0].z) == (200.0, 300.0, 160.0)
    assert (at[1].y, at[1].z) == (200.0, 160.0)  # not named, kept its y, raised all the same
    await p.stop()

  _run(_t())


def test_move_to_xy_positions_travels_without_raising_when_the_channels_are_high_enough():
  """Channels already at the height are not sent a Z move they do not need."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_location(
      [Coordinate(150.0, 250.0, 167.5), Coordinate(150.0, 200.0, 167.5)], use_channels=[0, 1]
    )
    sent = _record(p, names=True)
    await p.pipettes.move_to_xy_positions(200.0, {0: 300.0, 1: 250.0})
    assert "PrepMoveZAbsolute" not in sent
    assert sent.count("PrepMoveToPosition") == 1
    await p.stop()

  _run(_t())


def test_move_to_xy_positions_refuses_what_a_channel_cannot_reach():
  """The reach is checked before anything moves, the traverse height included."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    c = p.pipettes.configuration.channels[0]
    c.x_range, c.y_range, c.z_range = (0.0, 400.0), (0.0, 400.0), (0.0, 170.0)
    with pytest.raises(ValueError, match=r"x=500.0 outside channel 0"):
      await p.pipettes.move_to_xy_positions(500.0, {0: 100.0})
    with pytest.raises(ValueError, match=r"y=500.0 outside channel 0"):
      await p.pipettes.move_to_xy_positions(100.0, {0: 500.0})
    with pytest.raises(ValueError, match="no channel 5 on this Prep"):
      await p.pipettes.move_to_xy_positions(100.0, {5: 100.0})
    await p.stop()

  _run(_t())


def test_move_to_location_rises_travels_then_descends():
  """Three commands, in that order: the descent cannot be blended into the lateral move."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_location(
      [Coordinate(150.0, 250.0, 90.0), Coordinate(150.0, 200.0, 90.0)], use_channels=[0, 1]
    )
    sent = _record(p, names=True)
    await p.pipettes.move_to_location(
      [Coordinate(200.0, 300.0, 100.0), Coordinate(200.0, 250.0, 120.0)],
      use_channels=[0, 1],
      minimum_traverse_height_start=160.0,
    )
    moves = [c for c in sent if c in ("PrepMoveZAbsolute", "PrepMoveToPosition")]
    assert moves == ["PrepMoveZAbsolute", "PrepMoveToPosition", "PrepMoveZAbsolute"]
    at = await p.pipettes.request_locations()
    assert (at[0].x, at[0].y, at[0].z) == (200.0, 300.0, 100.0)
    assert (at[1].x, at[1].y, at[1].z) == (200.0, 250.0, 120.0)
    await p.stop()

  _run(_t())


def test_move_to_location_refuses_a_z_the_channel_cannot_reach_before_moving():
  """The target Z is checked with the rest, so nothing is raised for a move that cannot finish."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    p.pipettes.configuration.channels[0].z_range = (0.0, 170.0)
    sent = _record(p, names=True)
    with pytest.raises(ValueError, match=r"z=200.0 outside channel 0"):
      await p.pipettes.move_to_location(Coordinate(150.0, 250.0, 200.0), use_channels=0)
    with pytest.raises(ValueError, match="same x"):
      await p.pipettes.move_to_location(
        [Coordinate(100.0, 200.0, 100.0), Coordinate(101.0, 100.0, 100.0)], use_channels=[0, 1]
      )
    assert "PrepMoveZAbsolute" not in sent and "PrepMoveToPosition" not in sent
    await p.stop()

  _run(_t())


def test_one_channel_picks_up_a_spot_in_front_of_where_the_other_stands():
  """The channels share a gantry, so the one not picking up gives way rather than the call failing."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None

    # Channel 1 stands where channel 0 has to go: in front of the spot it is sent to.
    spot = rack.get_item("A1")
    y = spot.get_location_wrt(deck, "c", "c", "t").y
    await p.pipettes.move_to_y_positions({0: y + 30.0, 1: y + 20.0})

    await p.pipettes.pick_up_tips(rack["A1"], use_channels=[0])
    standing = await p.pipettes.request_locations()
    assert round(standing[0].y, 2) == round(y, 2)  # channel 0 arrived at the spot
    assert round(standing[1].y, 2) == round(y - 9.0, 2)  # channel 1 moved just out of its way

    # The move underneath still refuses on its own, where nothing has been asked to give way.
    await p.pipettes.drop_tips(rack["A1"], use_channels=[0])
    await p.pipettes.move_to_y_positions({0: y + 30.0, 1: y + 20.0})
    with pytest.raises(ValueError):
      await p.pipettes.move_to_xy_positions(100.0, {0: y})
    await p.stop()

  _run(_t())


@pytest.mark.parametrize("known_from", ["model", "sensors"])
def test_a_channel_that_carries_a_tip_is_refused_before_anything_moves(known_from):
  """A refusal moves nothing, whether the tip is in the model or only in the sleeve sensors - one
  left on from another session."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    if known_from == "model":
      await p.pipettes.pick_up_tips(rack["A1"], use_channels=[0])
    else:

      async def senses_one(*args, **kwargs):
        return [True, False]

      p.pipettes.sense_tip_presence = senses_one  # type: ignore[method-assign]
    sent = _record(p, names=True)
    with pytest.raises(HasTipError, match="senses a tip" if known_from == "sensors" else None):
      await p.pipettes.pick_up_tips(rack["A2"], use_channels=[0])
    assert "PrepMoveToPosition" not in sent  # refused where it stood
    assert "PrepPickUpTips" not in sent
    await p.stop()

  _run(_t())


def test_a_channel_the_sensors_say_lost_its_tip_is_not_asked_to_drop_one():
  """A tip knocked off on the way is in the sleeve sensors, not in the model."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.pick_up_tips(rack["A1"], use_channels=[0])
    assert p.pipettes.get_mounted_tip(0) is not None  # the model holds one

    async def senses_none(*args, **kwargs):
      return [False, False]

    p.pipettes.sense_tip_presence = senses_none  # type: ignore[method-assign]
    sent = _record(p, names=True)
    with pytest.raises(NoTipError, match="senses no tip"):
      await p.pipettes.drop_tips(rack["A1"], use_channels=[0])
    assert "PrepDropTips" not in sent
    await p.stop()

  _run(_t())


@pytest.mark.parametrize(
  "spots, picked_xs",
  [(("A1", "A2"), [[13.05], [22.05]]), (("A1", "B1"), [[13.05, 13.05]])],
)
def test_spots_are_picked_up_in_as_many_moves_as_their_x_positions_take(spots, picked_xs):
  """The channels ride one gantry: spots at two x are two moves, not a refusal, and spots in one
  column stay one move."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      if isinstance(command, PrepCmd.PrepPickUpTips):
        sent.append([round(t.x_position, 2) for t in command.tip_positions])
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.pick_up_tips(rack[spots], use_channels=[0, 1])
    assert sent == picked_xs
    assert [tip is not None for tip in p.pipettes.get_mounted_tips()] == [True, True]
    await p.pipettes.drop_tips(rack[spots], use_channels=[0, 1])
    assert p.pipettes.get_mounted_tips() == [None, None]
    await p.stop()

  _run(_t())


def test_z_positions_are_read_for_every_channel_in_one_command():
  """The same GetPositions that answers X and Y, in the deck's frame, recorded on each channel."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent = _record(p, names=True)
    zs = await p.pipettes.request_z_positions()
    assert sent == ["PrepGetPositions"]
    assert zs == [at.z for at in await p.pipettes.request_locations()]
    await p.stop()

  _run(_t())


def test_initializing_waits_longer_than_the_drivers_default():
  """A command that takes longer than any this device performs is one it will not answer."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    waited: list = []
    send = p.send_command

    async def record(command, *args, read_timeout=None, **kwargs):
      waited.append((type(command).__name__, read_timeout))
      return await send(command, *args, read_timeout=read_timeout, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.setup(force_initialize=True)
    assert [t for name, t in waited if "Initialize" in name] == [300.0]
    # Everything else names none, so it waits `default_read_timeout`.
    assert {t for name, t in waited if "Initialize" not in name} == {None}
    await p.stop()

  _run(_t())


def test_the_first_group_is_reached_at_the_starting_height_and_the_rest_at_the_one_between():
  """Two groups mean a raise between them, which is its own height, not the starting one."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None

    given: list = []
    one_move = p.pipettes._pick_up_tips_in_one_move

    async def record(*args, **kwargs):
      given.append(args[3] if len(args) > 3 else kwargs.get("minimum_traverse_height_start"))
      return await one_move(*args, **kwargs)

    p.pipettes._pick_up_tips_in_one_move = record  # type: ignore[method-assign]
    await p.pipettes.pick_up_tips(
      rack["A1", "A2"],
      use_channels=[0, 1],
      minimum_traverse_height_start=150.0,
      minimum_traverse_height_during=120.0,
    )
    assert given == [150.0, 120.0]  # to the first group, then between the groups
    await p.stop()

  _run(_t())


def test_the_starting_height_is_a_floor_and_leaves_a_higher_channel_where_it_is():
  """Below it is brought up; above it travels where it stands. Sending it would drive a high one down."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_tool_bottom_to_z_positions({1: 40.0})
    standing = await p.pipettes.request_locations()
    assert (round(standing[0].z, 2), round(standing[1].z, 2)) == (167.5, 40.0)

    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      if isinstance(command, PrepCmd.PrepMoveToPosition):
        sent.append(
          {a.channel: round(a.z_position, 2) for a in command.move_parameters.axis_parameters}
        )
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_to_xy_positions(
      120.0, {0: 200.0, 1: 150.0}, minimum_traverse_height_start=62.0
    )
    # Channel 0 is at 167.5, well above the floor, and stays there; channel 1 comes up to it.
    assert sent == [{2: 167.5, 1: 62.0}]
    after = await p.pipettes.request_locations()
    assert (round(after[0].z, 2), round(after[1].z, 2)) == (167.5, 62.0)
    await p.stop()

  _run(_t())


@pytest.mark.parametrize(
  "traverse, travel",
  [
    (None, ["PrepMoveZAbsolute", "PrepXAxisMoveAbsolute", "PrepMoveZAbsolute"]),
    (90.0, ["PrepXAxisMoveAbsolute"]),
  ],
)
def test_an_x_probe_given_a_start_travels_there_first_and_keeps_the_height_it_was_at(
  traverse, travel
):
  """A full arm move to the start - raising what is low unless a traverse height at the channel's
  own is given - then back down, so the search is X alone."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_location(
      Coordinate(150.0, 200.0, 90.0), use_channels=0, make_space=True
    )

    sent = _record(p, only=(PrepCmd.PrepXAxisMoveAbsolute, PrepCmd.PrepMoveZAbsolute), names=True)
    await p.pipettes.probe_x_using_clld(
      0,
      "left",
      search_start_position=200.0,
      search_end_position=190.0,
      minimum_traverse_height_start=traverse,
      allow_without_tip=True,
    )
    assert sent[: len(travel)] == travel
    standing = (await p.pipettes.request_locations())[0]
    assert round(standing.z, 1) == 90.0

    # An arm already at the start, to within what a device answers, is left where it is. Read
    # where it stands each time: the search before it left the arm where it stopped.
    for nudge in (0.0, 0.006):
      here = (await p.pipettes.request_locations())[0].x
      sent.clear()
      await p.pipettes.probe_x_using_clld(
        0,
        "left",
        search_start_position=here - nudge,
        search_end_position=here - 5.0,
        allow_without_tip=True,
      )
      assert "PrepMoveZAbsolute" not in sent
    standing = (await p.pipettes.request_locations())[0]
    assert round(standing.z, 1) == 90.0  # the height the caller had it at, not Z safety
    await p.stop()

  _run(_t())


@pytest.mark.parametrize(
  "traverse, travel",
  [
    (None, ["PrepMoveZAbsolute", "PrepMoveToPosition", "PrepMoveZAbsolute"]),
    (80.0, ["PrepMoveYAbsolute"]),
  ],
)
def test_a_y_probe_given_a_start_moves_there_first_and_makes_room_for_it(traverse, travel):
  """As the X probe travels to its start, this one moves there, its neighbour giving way; a
  traverse height at or below where the channel stands makes the step Y alone."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 340.0, 1: 320.0})
    await p.pipettes.move_tool_bottom_to_z_positions({1: 80.0})

    sent = _record(
      p,
      only=(PrepCmd.PrepMoveToPosition, PrepCmd.PrepMoveZAbsolute, PrepCmd.PrepMoveYAbsolute),
      names=True,
    )
    await p.pipettes.probe_y_using_clld(
      1,
      "forward",
      search_start_position=300.0,
      search_end_position=290.0,
      minimum_traverse_height_start=traverse,
      allow_without_tip=True,
    )
    assert sent == travel
    standing = (await p.pipettes.request_locations())[1]
    assert round(standing.z, 1) == 80.0

    # Already there: nothing is sent to move it again.
    sent.clear()
    here = (await p.pipettes.request_locations())[1].y
    await p.pipettes.probe_y_using_clld(
      1,
      "forward",
      search_start_position=here,
      search_end_position=here - 5.0,
      allow_without_tip=True,
    )
    assert sent == []
    await p.stop()

  _run(_t())


def test_probing_four_edges_comes_at_each_from_outside_and_lifts_between_them():
  """Back and front in Y, right and left in X, each approached from its own side."""

  async def _t():
    deck = PrepDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    target = Coordinate(150.0, 200.0, 60.0)

    pipettes = p.pipettes
    searched: list = []
    probe_y, probe_x = pipettes.probe_y_using_clld, pipettes.probe_x_using_clld

    async def record_y(channel, direction, **kwargs):
      standing = (await pipettes.request_locations())[channel]
      searched.append(("y", direction, round(standing.y, 1), round(standing.z, 1)))
      return await probe_y(channel, direction, **kwargs)

    async def record_x(channel, direction, **kwargs):
      standing = (await pipettes.request_locations())[channel]
      searched.append(("x", direction, round(standing.x, 1), round(standing.z, 1)))
      return await probe_x(channel, direction, **kwargs)

    pipettes.probe_y_using_clld = record_y  # type: ignore[method-assign, assignment]
    pipettes.probe_x_using_clld = record_x  # type: ignore[method-assign, assignment]
    found = await p.pipettes.probe_edges_using_clld(
      0, target, n_replicates=1, allow_without_tip=True
    )

    assert list(found) == ["back", "front", "right", "left"]
    assert all(len(measurements) == 1 for measurements in found.values())
    # Each search starts outside the target and 1 mm below its top, and none of them detects here.
    assert searched == [
      ("y", "forward", 215.0, 59.0),
      ("y", "backward", 191.0, 59.0),
      ("x", "left", 162.0, 59.0),
      ("x", "right", 138.0, 59.0),
    ]
    assert (await p.pipettes.request_locations())[0].z == pytest.approx(167.5)  # left at Z safety
    await p.stop()

  _run(_t())


def test_probing_four_edges_corrects_for_what_is_doing_the_touching():
  """The diameters reach both probes, which halve whichever of them is on the channel."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    pipettes = p.pipettes
    seen: list = []

    async def record(channel, direction, **kwargs):
      seen.append(
        (kwargs["tip_bottom_diameter"], kwargs["stop_disc_diameter"], kwargs["allow_without_tip"])
      )
      return None

    pipettes.probe_y_using_clld = record  # type: ignore[method-assign, assignment]
    pipettes.probe_x_using_clld = record  # type: ignore[method-assign, assignment]
    await pipettes.probe_edges_using_clld(
      0,
      Coordinate(150.0, 200.0, 60.0),
      n_replicates=1,
      tip_bottom_diameter=3.5,
      stop_disc_diameter=9.0,
      allow_without_tip=True,
    )

    assert seen == [(3.5, 9.0, True)] * 4  # every edge, X and Y alike
    await p.stop()

  _run(_t())


def test_probing_four_edges_refuses_a_bare_channel_before_it_moves():
  """The same refusal the searches make, made before the channel has gone anywhere; with a tip on,
  the probe runs."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    before = await p.pipettes.request_locations()
    sent = _record(p)
    with pytest.raises(RuntimeError, match="no tip on channel 0"):
      await p.pipettes.probe_edges_using_clld(0, Coordinate(150.0, 200.0, 60.0))
    assert not any(isinstance(c, PrepCmd.PrepMoveToPosition) for c in sent)
    assert await p.pipettes.request_locations() == before

    shaft = p.pipettes.shaft(0)
    assert shaft is not None
    shaft.mount_tip(hamilton_tip_300uL(name="tip"))
    found = await p.pipettes.probe_edges_using_clld(
      0, Coordinate(150.0, 200.0, 60.0), n_replicates=1
    )
    assert list(found) == ["back", "front", "right", "left"]
    await p.stop()

  _run(_t())


def test_tips_are_taken_where_their_collars_rest_not_where_their_bottoms_are():
  """A tip spot is the hole a tip hangs in, so the height sent is the spot itself: the moat floor,
  3.5, plus the rack's 55.0 to its top face, where the collars rest - probed at 58.22 to 58.42."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    sent = _record(p)

    await p.pipettes.pick_up_tips(rack["A1"], use_channels=[0])
    (pick,) = [c for c in sent if isinstance(c, PrepCmd.PrepPickUpTips)]
    (at,) = pick.tip_positions
    spot = rack.get_item("A1")
    assert at.z_position == spot.get_location_wrt(deck).z == 58.5
    assert at.z_seek == 71.5  # a collar and 5 mm above, clear of the tips' tops

    await p.pipettes.drop_tips(rack["A1"], use_channels=[0])
    (drop,) = [c for c in sent if isinstance(c, PrepCmd.PrepDropTips)]
    (back,) = drop.tip_positions
    assert (back.z_position, back.z_seek) == (58.5, 68.5)
    await p.stop()

  _run(_t())


def test_the_height_tips_are_taken_at_does_not_depend_on_how_long_they_are():
  """Three racks of one body, three tip lengths: the collars rest in the same hole, so one height.

  The STAR's recorded pick-ups say the same - one rack, four tip types, tz2164 every time - and it
  is the property the old Prep formula broke, by adding the tip's length to the spot.
  """

  async def _t():
    lengths, heights = set(), set()
    for rack_fn in (
      hamilton_96_tiprack_10uL_NTR,
      hamilton_96_tiprack_50uL_NTR,
      hamilton_96_tiprack_300uL_NTR,
    ):
      deck = PrepDeck()
      rack = deck[1] = rack_fn(name="tips", with_tips=True)
      p = PrepSimulationDriver(deck=deck)
      await p.setup()
      assert p.pipettes is not None
      sent: list = []
      send = p.send_command

      async def record(command, _send=send, _sent=sent, **kwargs):
        _sent.append(command)
        return await _send(command, **kwargs)

      p.send_command = record  # type: ignore[method-assign]
      await p.pipettes.pick_up_tips(rack["A1"], use_channels=[0])
      (pick,) = [c for c in sent if isinstance(c, PrepCmd.PrepPickUpTips)]
      lengths.add(rack.get_item("A1").make_tip().get_size_z())
      heights.add(pick.tip_positions[0].z_position)
      await p.stop()

    assert len(lengths) == 3
    assert heights == {58.5}

  _run(_t())


def test_the_teaching_needle_is_taken_where_the_device_takes_it():
  """Ground truth: 75.75 to pick up and to drop, the heights every run of the needle has used."""

  async def _t():
    deck = PrepDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    needle = deck.teaching_needle_spot
    assert needle is not None
    sent = _record(p)

    await p.pipettes.pick_up_tips([needle], use_channels=[0])
    (pick,) = [c for c in sent if isinstance(c, PrepCmd.PrepPickUpTips)]
    assert (pick.tip_positions[0].z_position, pick.tip_positions[0].z_seek) == (75.75, 88.75)

    await p.pipettes.drop_tips([needle], use_channels=[0])
    (drop,) = [c for c in sent if isinstance(c, PrepCmd.PrepDropTips)]
    assert (drop.tip_positions[0].z_position, drop.tip_positions[0].z_seek) == (75.75, 85.75)

    # And the needle stands where it stood: its body from the block's hole to 8 mm proud of its top.
    body = needle.make_tip()
    assert needle.get_location_wrt(deck).z - (body.get_size_z() - body.collar_height) == 23.85
    await p.stop()

  _run(_t())


def test_a_neighbour_in_the_way_of_a_named_search_end_stands_aside():
  """It alone goes to Z safety and moves just clear; the probing channel stays where it was put."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 290.0, 1: 275.56})
    await p.pipettes.move_tool_bottom_to_z_positions({0: 90.0})
    sent = _record(p)
    # Channel 1 sits 9 mm in front of 275.56, so the search would otherwise floor at 284.56.
    await p.pipettes.probe_y_using_clld(
      0, "forward", search_end_position=275.577, allow_without_tip=True
    )

    (raised,) = [c for c in sent if isinstance(c, PrepCmd.PrepMoveZUpToSafe)]
    assert list(raised.channels) == [p.pipettes.channel_enum(1)]  # only channel 1
    standing = await p.pipettes.request_locations()
    assert round(standing[1].y, 2) == 266.48  # just clear of the end, and no further
    assert round(standing[0].z, 2) == 90.0  # the probing channel was not lifted
    await p.stop()

  _run(_t())


def test_a_neighbour_already_clear_of_the_search_is_left_alone():
  """Room it already has is room enough: nothing is raised and nothing is moved."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 290.0, 1: 200.0})
    sent = _record(p)
    await p.pipettes.probe_y_using_clld(
      0, "forward", search_end_position=280.0, allow_without_tip=True
    )

    assert not [c for c in sent if isinstance(c, PrepCmd.PrepMoveZUpToSafe)]
    standing = await p.pipettes.request_locations()
    assert round(standing[1].y, 2) == 200.0
    await p.stop()

  _run(_t())


def test_a_neighbour_that_cannot_stand_clear_refuses_the_search():
  """It would have to leave its own Y window to make the room, so the search is refused instead."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 290.0, 1: 281.0})
    # A channel that cannot go far forward. This unit's two windows are offset by the spacing
    # itself, so no end channel 0 can reach leaves channel 1 nowhere to stand; the spacing is held
    # at 9 mm here so that a hemmed-in channel can be given one.
    p.pipettes._min_spacing_between = lambda i, j: 9.0  # type: ignore[method-assign]
    p.pipettes.configuration.channels[1].y_range = (270.0, 376.0)
    sent = _record(p)
    with pytest.raises(ValueError, match=r"y=255.9 outside channel 1 range"):
      await p.pipettes.probe_y_using_clld(
        0, "forward", search_end_position=265.0, allow_without_tip=True
      )
    assert not [c for c in sent if isinstance(c, PrepCmd.PrepMoveZUpToSafe)]
    await p.stop()

  _run(_t())


def test_a_channel_carrying_a_tip_travels_as_high_as_it_goes_not_to_the_traverse_height():
  """The tool bottom cannot reach the traverse height with a tip on: what it holds hangs below it.

  The device answers 0x0011 GenericInvalidParameter for a tool bottom of 167.5 while a 51.9 mm
  needle is on, because the drive would have to reach 219.4.
  """

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    pipettes = p.pipettes
    sent: list = []
    travelled: list = []
    send = p.send_command

    async def record(command, **kwargs):
      if isinstance(command, PrepCmd.PrepMoveZAbsolute):
        # to 0.01 mm: the device's tip lengths come back as float32, so 51.9 is 51.900001525878906
        sent.append({int(c.channel): round(c.z_position, 2) for c in command.channels})
      if isinstance(command, PrepCmd.PrepMoveToPosition):
        travelled.append(
          {int(a.channel): round(a.z_position, 2) for a in command.move_parameters.axis_parameters}
        )
      return await send(command, **kwargs)

    p.send_command = record  # type: ignore[method-assign]

    await pipettes.move_to_y_positions({0: 250.0, 1: 200.0})
    await pipettes.move_tool_bottom_to_z_positions({0: 90.0, 1: 90.0})
    sent.clear()
    await pipettes.move_to_xy_positions(150.0, {0: 250.0})
    assert sent == [{2: 167.5, 1: 167.5}]  # nothing held, so both go to the traverse height

    shaft = pipettes.shaft(0)
    assert shaft is not None
    shaft.mount_tip(hamilton_tip_300uL(name="tip"))  # 51.9 mm below the stop disc
    # A pick-up reads the windows again, and this test mounts behind the driver's back.
    await pipettes._record_channel_bounds()
    await pipettes.move_tool_bottom_to_z_positions({0: 90.0})
    sent.clear()
    travelled.clear()
    await pipettes.move_to_xy_positions(100.0, {0: 250.0})
    assert sent == [{2: 115.6, 1: 167.5}]  # 167.5 - 51.9 for the carrying channel, alone
    # The raise and the travel carry their heights separately, so both are held here. The raise
    # covers every channel below its ceiling; the travel carries only the ones moving.
    assert travelled == [{2: 115.6}]

    sent.clear()
    await pipettes.move_to_xy_positions(150.0, {0: 250.0})
    assert sent == []  # already at its ceiling, so it is not asked to move at all
    await p.stop()

  _run(_t())


def test_the_stop_disc_move_takes_off_what_the_channel_carries():
  """The firmware positions the tool bottom, so a stop-disc target is sent an overhang lower."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    pipettes = p.pipettes
    sent: list = []
    send = p.send_command

    async def record(command, **kwargs):
      if isinstance(command, PrepCmd.PrepMoveZAbsolute):
        sent.append({int(c.channel): round(c.z_position, 2) for c in command.channels})
      return await send(command, **kwargs)

    p.send_command = record  # type: ignore[method-assign]

    # Carrying nothing, the stop disc and the tool bottom are the same point.
    await pipettes.move_stop_disc_to_z_positions({0: 150.0})
    assert sent == [{2: 150.0, 1: 167.5}]

    shaft = pipettes.shaft(0)
    assert shaft is not None
    shaft.mount_tip(hamilton_tip_300uL(name="tip"))  # 51.9 mm below the stop disc
    sent.clear()
    await pipettes.move_stop_disc_to_z_positions({0: 150.0})
    assert sent == [{2: 98.1, 1: 167.5}]  # 150 - 51.9

    sent.clear()
    await pipettes.move_stop_disc_to_z_position(0, 140.0)
    assert sent == [{2: 88.1, 1: 167.5}]  # the singular is the plural with one channel

    # Every channel that does not exist is named, not just the first.
    with pytest.raises(ValueError, match=r"channels must be between 0 and 1, are \[5, 7\]"):
      await pipettes.move_stop_disc_to_z_positions({5: 100.0, 7: 100.0})
    await p.stop()

  _run(_t())


def test_setup_discards_a_tip_it_finds_already_attached():
  """A session that connects to a machine still holding a tip clears it before anything else."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    shaft = p.pipettes.shaft(0)
    assert shaft is not None
    await p.stop()
    # A crash leaves a tip on without stop() running, which would have cleared it.
    shaft.mount_tip(hamilton_tip_300uL(name="left on"))

    sent = _record(p)
    await p.setup(smart=True)  # and the next one connects, to a machine still holding it
    assert p.pipettes is not None

    assert [c for c in sent if isinstance(c, PrepCmd.PrepDropTips)]  # into the waste
    assert not (await p.pipettes.sense_tip_presence())[0]
    assert p.pipettes.get_mounted_tip(0) is None
    await p.stop()

  _run(_t())


def test_setup_discards_tips_it_finds_on_both_channels():
  """Each channel found carrying gets a tip of its own in the model, so both are dropped."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    shafts = [p.pipettes.shaft(ch) for ch in range(2)]
    await p.stop()
    for ch, shaft in enumerate(shafts):
      assert shaft is not None
      shaft.mount_tip(hamilton_tip_50uL(name=f"left on {ch}"))

    sent = _record(p)
    await p.setup(smart=True)
    assert p.pipettes is not None

    drops = [c for c in sent if isinstance(c, PrepCmd.PrepDropTips)]
    assert len(drops) == 1 and len(drops[0].tip_positions) == 2  # both, in one move
    assert await p.pipettes.sense_tip_presence() == [False, False]
    assert all(p.pipettes.get_mounted_tip(ch) is None for ch in range(2))
    await p.stop()

  _run(_t())


def _refusal(code: int, action: int) -> HoiError:
  """A device refusal carrying `code`, as the session raises it."""
  entry = HcResultEntry(
    module_id=0xE000, node_id=1, object_id=0x1000, interface_id=1, action_id=action, result=code
  )
  return HoiError(exceptions={0: RuntimeError(hex(code))}, entries=[entry], raw_response=b"")


def test_setup_treats_a_stale_tool_definition_as_the_tips_that_are_on():
  """A refused tool pickup leaves its definition behind: the device says no tool is held, and the
  Z window says what is really on, so those are dropped as tips."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.core_grippers is not None
    shafts = [p.pipettes.shaft(ch) for ch in range(2)]
    await p.stop()
    for ch, shaft in enumerate(shafts):
      assert shaft is not None
      shaft.mount_tip(hamilton_tip_50uL(name=f"left on {ch}"))

    stale = PrepCmd.TipDefinition(
      default_values=False,
      id=255,
      volume=1.0,
      length=22.9,
      tip_type=0,
      has_filter=False,
      is_needle=False,
      is_tool=True,
      label="Pipettor Custom",
    )

    async def stale_definition(channel: int):
      return stale

    async def no_tool_held(**kwargs):
      raise _refusal(0x0F07, 16)

    p.pipettes.request_attached_tip_information = stale_definition  # type: ignore[method-assign]
    p.core_grippers.drop_tools = no_tool_held  # type: ignore[method-assign]
    sent = _record(p)
    await p.setup(smart=True)
    assert p.pipettes is not None

    drops = [c for c in sent if isinstance(c, PrepCmd.PrepDropTips)]
    assert len(drops) == 1 and len(drops[0].tip_positions) == 2
    assert not [c for c in sent if isinstance(c, PrepCmd.PrepReleaseTips)]  # the drop sufficed
    assert await p.pipettes.sense_tip_presence() == [False, False]
    await p.stop()

  _run(_t())


def test_setup_releases_over_the_waste_when_the_device_refuses_to_drop():
  """Last resort: a drop the Pipettor refuses (it has forgotten the tips) ends with the squeeze
  release over the waste, after the channels were taken there."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    shafts = [p.pipettes.shaft(ch) for ch in range(2)]
    await p.stop()
    for ch, shaft in enumerate(shafts):
      assert shaft is not None
      shaft.mount_tip(hamilton_tip_50uL(name=f"left on {ch}"))

    async def no_tips_held(*args, **kwargs):
      raise _refusal(0x0F03, 14)

    p.pipettes.drop_tips = no_tips_held  # type: ignore[method-assign]
    sent = _record(p)
    await p.setup(smart=True)
    assert p.pipettes is not None

    names = [type(c).__name__ for c in sent]
    assert "PrepXAxisMoveAbsolute" in names and names.count("PrepYAxisMoveRelative") == 2
    assert names.count("PrepReleaseTips") == 2
    assert names.index("PrepXAxisMoveAbsolute") < names.index("PrepReleaseTips")
    assert await p.pipettes.sense_tip_presence() == [False, False]
    await p.stop()

  _run(_t())


def test_setup_puts_a_tool_it_finds_attached_back_rather_than_discarding_it():
  """A grip tool is not a tip: it goes back in its holder, and no drop into the waste is sent."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck(with_core_grippers=True))
    await p.setup()
    assert p.pipettes is not None
    shaft = p.pipettes.shaft(0)
    assert shaft is not None
    shaft.mount_tip(hamilton_core_gripper_tool(name="left on"))
    await p.stop()  # the session ends without putting it back

    sent = _record(p)
    await p.setup(smart=True)  # and the next one connects, to a machine still holding it

    assert [c for c in sent if isinstance(c, PrepCmd.PrepDropTool)]
    assert not [c for c in sent if isinstance(c, PrepCmd.PrepDropTips)]
    await p.stop()

  _run(_t())


def test_a_drop_travels_over_its_destination_before_letting_go():
  """Left to itself the gantry drives X, Y and Z at once, arcing the tips through the deck."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    pipettes = p.pipettes
    await pipettes.pick_up_tips(rack["A1", "B1"], use_channels=[0, 1])

    sent = _record(p, only=(PrepCmd.PrepMoveToPosition, PrepCmd.PrepDropTips), names=True)

    await pipettes.drop_tips(rack["A1", "B1"], use_channels=[0, 1])
    assert sent == ["PrepMoveToPosition", "PrepDropTips"]  # over them first, then straight down

    # Into the waste, which has its own position per channel and no planning.
    await pipettes.pick_up_tips(rack["A2", "B2"], use_channels=[0, 1])
    sent.clear()
    waste = deck.waste_block
    assert waste is not None
    await pipettes.drop_tips([waste, waste], use_channels=[0, 1])
    assert sent == ["PrepMoveToPosition", "PrepDropTips"]

    # Two groups, too far apart in x to share a move: each is travelled to.
    await pipettes.pick_up_tips(rack["A3", "B3"], use_channels=[0, 1])
    sent.clear()
    await pipettes.drop_tips(rack["A3", "B4"], use_channels=[0, 1])
    assert sent == [
      "PrepMoveToPosition",
      "PrepDropTips",
      "PrepMoveToPosition",
      "PrepDropTips",
    ]
    await p.stop()

  _run(_t())


def test_discard_tips_drops_what_the_channels_carry_into_the_waste():
  """Only the channels carrying a tip go, in one move; nothing carried, nothing sent."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_50uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    pipettes = p.pipettes
    sent = _record(p, only=(PrepCmd.PrepMoveToPosition, PrepCmd.PrepDropTips), names=True)

    await pipettes.discard_tips()
    assert sent == []

    await pipettes.pick_up_tips(rack["A1"], use_channels=[1])
    sent.clear()
    await pipettes.discard_tips()
    assert sent == ["PrepMoveToPosition", "PrepDropTips"]
    assert pipettes.get_mounted_tip(1) is None
    await p.stop()

  _run(_t())


def _shaft_end_z(p: PrepSimulationDriver, channel: int) -> float:
  """Where the model has the end of a channel's shaft, in mm on the deck."""
  assert p.pipettes is not None and p.pipettes.deck is not None
  resource = p.pipettes.resources[channel]
  return resource.get_location_wrt(p.pipettes.deck).z + p.pipettes._reference_anchor(resource).z


def test_z_is_where_the_device_reports_it_at_the_bottom_of_what_a_channel_carries():
  """A reported Z is the tip's bottom when there is one: the shaft ends that much higher."""

  async def _t():
    deck = PrepDeck()
    rack = deck[1] = hamilton_96_tiprack_300uL_NTR(name="tips", with_tips=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    pipettes = p.pipettes

    pipettes.update_location_by_reference_point(0, z=100.0)
    assert _shaft_end_z(p, 0) == pytest.approx(100.0)

    await pipettes.pick_up_tips(rack["A1"], use_channels=[0])
    tip = pipettes.get_mounted_tip(0)
    assert tip is not None
    below = tip.get_size_z() - tip.fitting_depth
    pipettes.update_location_by_reference_point(0, z=100.0)
    assert _shaft_end_z(p, 0) == pytest.approx(100.0 + below)
    point = pipettes.get_reference_point_location(0)
    assert point is not None and point.z == pytest.approx(100.0)
    assert (await pipettes.request_locations())[0].z == pytest.approx(100.0)

    await pipettes.drop_tips(rack["A1"], use_channels=[0])
    pipettes.update_location_by_reference_point(0, z=100.0)
    assert _shaft_end_z(p, 0) == pytest.approx(100.0)
    await p.stop()

  _run(_t())


def test_every_y_and_z_move_is_recorded_from_where_the_device_says_the_channels_are():
  """Done, failed or cancelled: the model is read back from the device, never set from the target."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    pipettes = p.pipettes
    reads: List[str] = []
    record = pipettes._record_where_they_stopped

    async def recording() -> None:
      reads.append("read")
      await record()

    pipettes._record_where_they_stopped = recording  # type: ignore[method-assign]
    await pipettes.move_tool_bottom_to_z_positions({0: 150.0})
    await pipettes.move_to_y_positions({0: 370.0})
    assert reads == ["read", "read"]

    for name, move in (
      (
        "_unchecked_fw_move_z_absolute",
        lambda: pipettes.move_tool_bottom_to_z_positions({0: 140.0}),
      ),
      ("_unchecked_fw_move_y_absolute", lambda: pipettes.move_to_y_positions({0: 365.0})),
    ):
      reads.clear()

      async def cancelled(*args: Any, **kwargs: Any) -> None:
        raise asyncio.CancelledError()

      setattr(pipettes, name, cancelled)
      with pytest.raises(asyncio.CancelledError):
        await move()
      delattr(pipettes, name)
      assert reads == ["read"], name

    # The arm's move reads the channels as well as the arm: once before, to see what to raise (nothing,
    # here), and once after, where it stopped.
    await pipettes.move_to_safe_z()
    located = pipettes.request_locations
    asked: List[str] = []

    async def locations() -> List[Coordinate]:
      asked.append("pipettes")
      return await located()

    pipettes.request_locations = locations  # type: ignore[method-assign]
    await p.x_arm.move_to_x_position(150.0, minimum_traverse_height_start=0)
    assert asked == ["pipettes", "pipettes"]
    await p.stop()

  _run(_t())
