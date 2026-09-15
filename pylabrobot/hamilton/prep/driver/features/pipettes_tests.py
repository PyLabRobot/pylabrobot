"""PipetteChannel facade + enumeration against the simulator."""

from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.pipettes import PipetteChannel, Pipettes
from pylabrobot.hamilton.prep.driver.simulator import (
  SIMULATED_X_VELOCITY,
  SIMULATED_Y_DRIVE_OFFSETS,
)
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import PrepDeck, STARLetDeck, hamilton_96_tiprack_50uL_NTR
from pylabrobot.resources.tip_tracker import set_tip_tracking
from pylabrobot.resources.volume_tracker import set_volume_tracking


def _run(coro):
  asyncio.run(coro)


def test_channels_match_configuration_num_channels():
  """Pipettes.channels length matches the configuration's num_channels on a default simulator."""

  async def _t():
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup()
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    assert p.configuration is not None
    assert len(p.pipettes.channels) == p.configuration.num_channels
    for i, ch in enumerate(p.pipettes.channels):
      assert isinstance(ch, PipetteChannel)
      assert ch.index == i
    await p.stop()

  _run(_t())


def test_channels_attach_the_bounds_the_device_answers():
  """Each channel carries the bounds GetChannelBounds answers: on the simulator, the recorded ones."""

  async def _t():
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup()
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    for ch, recorded in zip(p.pipettes.channels, p.pipettes.configuration.channels):
      assert ch.bounds is not None
      assert (ch.bounds["y_min"], ch.bounds["y_max"]) == recorded.y_range
    await p.stop()

  _run(_t())


def test_channels_tip_trackers_pick_and_drop():
  """pick_up_tips / drop_tips update spot + channel TipTrackers when tip tracking is on."""

  async def _t():
    set_tip_tracking(True)
    try:
      deck = PrepDeck()
      tip_rack = deck[3] = hamilton_96_tiprack_50uL_NTR(name="ntr", with_tips=True)
      p = PrepSimulationDriver(deck=deck)
      await p.setup()
      assert p.pipettes is not None
      spots = [tip_rack.get_item("A1"), tip_rack.get_item("B1")]
      n = min(2, p.pipettes.num_channels)
      spots = spots[:n]
      use = list(range(n))
      assert all(s.has_tip() for s in spots)
      assert all(t is None for t in p.pipettes.get_mounted_tips()[:n])

      await p.pipettes.pick_up_tips(spots, use_channels=use)
      assert all(not s.has_tip() for s in spots)
      mounted = p.pipettes.get_mounted_tips()
      assert all(mounted[i] is not None for i in use)
      assert all(p.pipettes.head[i].has_tip for i in use)

      await p.pipettes.drop_tips(spots, use_channels=use)
      assert all(s.has_tip() for s in spots)
      assert all(not p.pipettes.head[i].has_tip for i in use)
      await p.stop()
    finally:
      set_tip_tracking(False)

  _run(_t())


def test_channels_volume_trackers_aspirate_dispense():
  """aspirate/dispense update well and tip VolumeTrackers when volume tracking is on."""

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
      n = min(2, p.pipettes.num_channels)
      spots = [tip_rack.get_item("A1"), tip_rack.get_item("B1")][:n]
      use = list(range(n))
      src = plate["A1:B1"][:n]
      dst = plate["A7:B7"][:n]
      vols = [20.0] * n
      for well in src:
        well.tracker.set_volume(100.0)

      await p.pipettes.pick_up_tips(spots, use_channels=use)
      await p.pipettes.aspirate(
        src,
        vols=vols,
        use_channels=use,
        disable_volume_correction=[True] * n,
      )
      for well in src:
        assert well.tracker.get_used_volume() == pytest.approx(80.0)
      for ch in use:
        tip = p.pipettes.head[ch].get_tip()
        assert tip.tracker.get_used_volume() == pytest.approx(20.0)

      await p.pipettes.dispense(
        dst,
        vols=vols,
        use_channels=use,
        disable_volume_correction=[True] * n,
      )
      for well in dst:
        assert well.tracker.get_used_volume() == pytest.approx(20.0)
      for ch in use:
        tip = p.pipettes.head[ch].get_tip()
        assert tip.tracker.get_used_volume() == pytest.approx(0.0)

      await p.pipettes.drop_tips(spots, use_channels=use)
      await p.stop()
    finally:
      set_tip_tracking(False)
      set_volume_tracking(False)

  _run(_t())


def test_configuration_holds_one_entry_per_channel_and_setup_choices():
  """Setup sizes `configuration.channels` against the device, and keeps what the caller chose."""

  async def _t():
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup(default_traverse_height=150.0)
    assert p.pipettes is not None
    c = p.pipettes.configuration
    assert len(c.channels) == p.num_channels
    assert p.pipettes.default_minimum_traverse_height == 150.0
    assert c.use_v1_aspirate_dispense is False
    assert c.supports_v2_pipetting is True
    await p.stop()

  _run(_t())


def test_move_to_location_refuses_what_the_channel_bounds_exclude():
  """The reach recorded on a channel's configuration guards the move before anything is sent."""

  async def _t():
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup()
    assert p.pipettes is not None
    c = p.pipettes.configuration.channels[0]
    c.x_range, c.y_range, c.z_range = (0.0, 400.0), (0.0, 400.0), (0.0, 170.0)
    with pytest.raises(ValueError, match="outside channel 0"):
      await p.pipettes.move_to_location(Coordinate(500.0, 100.0, 100.0), use_channels=0)
    with pytest.raises(ValueError, match="above channel 0 maximum"):
      await p.pipettes.move_to_location(Coordinate(100.0, 100.0, 200.0), use_channels=0)
    with pytest.raises(ValueError, match="same x"):
      await p.pipettes.move_to_location(
        [Coordinate(100.0, 200.0, 100.0), Coordinate(101.0, 100.0, 100.0)], use_channels=[0, 1]
      )
    await p.stop()

  _run(_t())


def test_move_to_y_positions_moves_each_named_channel_in_one_command():
  """Each channel goes to its own Y in a single move, keeping its X and Z."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    before = await p.pipettes.request_locations()
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_to_y_positions({1: 75.0, 0: 100.0})
    assert sent.count("PrepMoveYAbsolute") == 1
    assert "PrepMoveToPosition" not in sent
    after = await p.pipettes.request_locations()
    assert (after[0].y, after[0].z) == (100.0, before[0].z)
    assert (after[1].y, after[1].z) == (75.0, before[1].z)
    assert after[0].x == after[1].x == before[0].x
    with pytest.raises(ValueError, match="out of range"):
      await p.pipettes.move_to_y_positions({2: 50.0})
    await p.stop()

  _run(_t())


def test_move_tool_bottom_to_z_positions_moves_each_named_channel_in_one_command():
  """Each channel goes to its own Z in a single move, keeping its X and Y."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    before = await p.pipettes.request_locations()
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_tool_bottom_to_z_positions({1: 75.0, 0: 50.0})
    assert sent.count("PrepMoveZAbsolute") == 1
    assert "PrepMoveToPosition" not in sent
    after = await p.pipettes.request_locations()
    assert (after[0].y, after[0].z) == (before[0].y, 50.0)
    assert (after[1].y, after[1].z) == (before[1].y, 75.0)
    with pytest.raises(ValueError, match="out of range"):
      await p.pipettes.move_tool_bottom_to_z_positions({2: 50.0})
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


def test_move_to_location_sets_speed_scales_for_the_move_and_puts_them_back():
  """A speed scale is read, set, the move sent, and the earlier scale restored - in that order."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_to_location(
      Coordinate(150.0, 360.0, 160.0), use_channels=0, x_speed_scale=25, z_speed_scale=50
    )
    names = [type(c).__name__ for c in sent]
    move = names.index("PrepMoveToPosition")
    # The front channel's position is read first, to check the rear channel keeps its spacing from it.
    assert names[:move] == [
      "PrepGetPositions",
      "PrepGetXSpeedScale",
      "PrepSetXSpeedScale",
      "PrepGetZSpeedScale",
      "PrepSetZSpeedScale",
    ]
    assert (sent[2].value, sent[4].value) == (25, 50)
    assert names[move + 1 : move + 3] == ["PrepSetXSpeedScale", "PrepSetZSpeedScale"]
    assert (sent[move + 1].value, sent[move + 2].value) == (100, 100)

    with pytest.raises(ValueError, match="between 1 and 100"):
      await p.pipettes.move_to_location(Coordinate(150.0, 360.0, 160.0), x_speed_scale=0)
    await p.stop()

  _run(_t())


def test_move_to_location_x_speed_is_set_as_the_nearest_scale():
  """x_speed in mm/s becomes the X speed scale; it and x_speed_scale cannot both be given."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_to_location(Coordinate(150.0, 360.0, 160.0), x_speed=150.0)
    scales = [c.value for c in sent if type(c).__name__ == "PrepSetXSpeedScale"]
    assert scales == [25, 100]
    with pytest.raises(ValueError, match="not both"):
      await p.pipettes.move_to_location(
        Coordinate(150.0, 360.0, 160.0), x_speed=150.0, x_speed_scale=25
      )
    with pytest.raises(ValueError, match="between 6.0 and 400.0 mm/s"):
      await p.pipettes.move_to_location(Coordinate(150.0, 360.0, 160.0), x_speed=500.0)
    await p.stop()

  _run(_t())


def test_move_to_location_uses_default_x_speed_when_none_is_given():
  """With no speed named, the move is sent at `default_x_speed`, and a changed default is honoured."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_to_location(Coordinate(150.0, 360.0, 160.0))
    p.pipettes.default_x_speed = 60.0
    await p.pipettes.move_to_location(Coordinate(200.0, 360.0, 160.0))
    scales = [c.value for c in sent if type(c).__name__ == "PrepSetXSpeedScale"]
    assert scales == [53, 100, 10, 100]
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
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
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
    await p.stop()

  _run(_t())


def test_y_and_z_moves_send_the_speed_they_are_given():
  """A speed named on any of the four Y and Z moves is the velocity sent; one not above 0 is refused unsent."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
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
      lambda: p.pipettes.move_tool_bottom_to_z_positions({0: 150.0}, speed=0),
      lambda: p.pipettes.move_tool_bottom_to_z_position(0, 150.0, speed=-1.0),
    ):
      with pytest.raises(ValueError, match="speed must be above 0 mm/s"):
        await refused()
    assert sent == []
    await p.stop()

  _run(_t())


def test_z_moves_hold_the_acceleration_for_the_move_and_put_it_back():
  """Each Z drive's acceleration is read and set before the move and set back after it, even when it fails; with
  none named nothing is sent, and one not above 0 is refused unsent."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    acceleration_commands = (
      PrepCmd.PrepZDriveGetAcceleration,
      PrepCmd.PrepZDriveSetAcceleration,
      PrepCmd.PrepMoveZAbsolute,
    )

    def sequence():
      return [
        (
          type(c).__name__,
          str(c.dest) if not isinstance(c, PrepCmd.PrepMoveZAbsolute) else None,
          getattr(c, "value", None),
        )
        for c in sent
        if isinstance(c, acceleration_commands)
      ]

    drives = [str(a) for a in (await p.request_channel_drives()).zdrive_addrs]
    await p.pipettes.move_tool_bottom_to_z_positions({0: 150.0, 1: 140.0}, acceleration=400.0)
    assert sequence() == [
      ("PrepZDriveGetAcceleration", drives[0], None),
      ("PrepZDriveSetAcceleration", drives[0], 400.0),
      ("PrepZDriveGetAcceleration", drives[1], None),
      ("PrepZDriveSetAcceleration", drives[1], 400.0),
      ("PrepMoveZAbsolute", None, None),
      ("PrepZDriveSetAcceleration", drives[0], 800.0),
      ("PrepZDriveSetAcceleration", drives[1], 800.0),
    ]

    sent.clear()
    await p.pipettes.move_tool_bottom_to_z_position(0, 160.0)
    assert [name for name, _, _ in sequence()] == ["PrepMoveZAbsolute"]

    sent.clear()
    p.pipettes._unchecked_fw_move_z_absolute = AsyncMock(side_effect=RuntimeError("stuck"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="stuck"):
      await p.pipettes.move_tool_bottom_to_z_position(1, 150.0, acceleration=300.0)
    assert [
      (name, value) for name, _, value in sequence() if name == "PrepZDriveSetAcceleration"
    ] == [
      ("PrepZDriveSetAcceleration", 300.0),
      ("PrepZDriveSetAcceleration", 300.0),
      ("PrepZDriveSetAcceleration", 800.0),
      ("PrepZDriveSetAcceleration", 800.0),
    ]

    sent.clear()
    for refused in (
      lambda: p.pipettes.move_tool_bottom_to_z_positions({0: 150.0}, acceleration=0),
      lambda: p.pipettes.move_tool_bottom_to_z_position(0, 150.0, acceleration=-100.0),
    ):
      with pytest.raises(ValueError, match="acceleration must be above 0 mm/s2"):
        await refused()
    assert sequence() == []
    await p.stop()

  _run(_t())


def test_moves_that_keep_x_leave_the_x_speed_scale_alone():
  """A Y or Z move keeps the gantry where it stands, so no X speed scale is read, set or put back."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 280.0})
    await p.pipettes.move_tool_bottom_to_z_positions({0: 150.0, 1: 140.0})
    assert {"PrepMoveYAbsolute", "PrepMoveZAbsolute"} <= set(sent)
    assert not [name for name in sent if "XSpeedScale" in name]
    await p.stop()

  _run(_t())


def test_clld_probe_y_searches_along_y_where_the_channel_stands():
  """The Y seek is sent at the channel's X and Z from its start to its end; nothing detected raises."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 200.0})
    here = (await p.pipettes.request_locations())[1]
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="detected nothing between y=200.00 and y=150.00 mm"):
      await p.pipettes.clld_probe_y_position_using_channel(
        1, "forward", end_pos_search=150.0, speed=5.0
      )
    seek = next(c for c in sent if isinstance(c, PrepCmd.PrepYSeekLldPosition)).seek_parameters
    assert (seek.start_position_x, seek.start_position_y, seek.start_position_z) == (
      here.x,
      200.0,
      here.z,
    )
    assert (seek.seek_position_y, seek.seek_velocity_y) == (150.0, 5.0)
    assert int(seek.channel) == p.pipettes.channel_enum(1)
    assert (seek.lld_sensitivity, seek.detect_mode) == (
      p.pipettes.default_clld_sensitivity,
      p.pipettes.default_clld_detect_mode,
    )
    assert (await p.pipettes.request_locations())[1].y == 150.0
    await p.stop()

  _run(_t())


def test_clld_probe_y_returns_the_surface_and_backs_off():
  """A detection returns where the channel stopped less half the tip diameter, and backs the channel off."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 200.0})
    touched = PrepCmd.SeekResultParameters(
      default_values=False, channel=p.pipettes.channel_enum(1), detected=True, position=0.0
    )
    p.pipettes._unchecked_fw_y_seek_lld_position = AsyncMock(return_value=touched)  # type: ignore[method-assign]
    surface = await p.pipettes.clld_probe_y_position_using_channel(
      1, "forward", end_pos_search=150.0
    )
    assert surface == pytest.approx(200.0 - 0.6)
    assert (await p.pipettes.request_locations())[1].y == pytest.approx(202.0)
    surface = await p.pipettes.clld_probe_y_position_using_channel(
      0, "backward", tip_bottom_diameter=2.0
    )
    assert surface == pytest.approx(300.0 + 1.0)
    assert (await p.pipettes.request_locations())[0].y == pytest.approx(298.0)
    await p.stop()

  _run(_t())


def test_probe_y_using_clld_searches_with_the_channels_own_y_axis():
  """The channel's Y axis is sent the end of the search in its drive frame; nothing detected raises."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    assert [(c.yaxis, c.ydrive) for c in p.pipettes.channels] == [
      (Address(1, node, 259), Address(1, node, 515)) for node in (236, 238)
    ]
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 200.0})
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="detected nothing between y=200.00 and y=150.00 mm"):
      await p.pipettes.probe_y_using_clld(1, "forward", end_pos_search=150.0, speed=5.0)
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


def test_probe_y_using_clld_returns_the_surface_where_it_triggered_and_backs_off():
  """A detection returns the trigger position in deck Y less half the tip diameter, and backs the channel off."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 200.0})
    Response = PrepCmd.PrepYAxisSeekCapacitiveLld.Response
    p.pipettes._unchecked_fw_y_axis_seek_capacitive_lld = AsyncMock(  # type: ignore[method-assign]
      return_value=Response(lld_detected=True, detect_position=190.0 + SIMULATED_Y_DRIVE_OFFSETS[1])
    )
    surface = await p.pipettes.probe_y_using_clld(1, "forward", end_pos_search=150.0)
    assert surface == pytest.approx(190.0 - 0.6)
    assert (await p.pipettes.request_locations())[1].y == pytest.approx(192.0)
    p.pipettes._unchecked_fw_y_axis_seek_capacitive_lld = AsyncMock(  # type: ignore[method-assign]
      return_value=Response(lld_detected=True, detect_position=305.0 + SIMULATED_Y_DRIVE_OFFSETS[0])
    )
    surface = await p.pipettes.probe_y_using_clld(0, "backward", tip_bottom_diameter=2.0)
    assert surface == pytest.approx(305.0 + 1.0)
    assert (await p.pipettes.request_locations())[0].y == pytest.approx(303.0)
    await p.stop()

  _run(_t())


def test_clld_probe_y_refuses_searches_it_cannot_make():
  """Out of range, into a neighbour, backwards, or without speed: refused before anything is sent."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_y_positions({0: 300.0, 1: 200.0})
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    for probe in (p.pipettes.clld_probe_y_position_using_channel, p.pipettes.probe_y_using_clld):
      for call, message in (
        (
          lambda: probe(1, "backward", end_pos_search=295.0),
          "outside the range channel 1 may search",
        ),
        (lambda: probe(1, "backward", end_pos_search=150.0), "cannot end at"),
        (lambda: probe(1, "sideways", end_pos_search=150.0), "probing_direction"),  # type: ignore[arg-type]
        (lambda: probe(1, "forward", speed=0), "speed must be above 0"),
        (lambda: probe(2, "forward"), "channel_idx must be between"),
      ):
        with pytest.raises(ValueError, match=message):
          await call()
    assert not {"PrepYSeekLldPosition", "PrepYAxisSeekCapacitiveLld", "PrepMoveYAbsolute"} & set(
      sent
    )
    await p.stop()

  _run(_t())


def test_probe_x_using_clld_steps_the_arm_with_detection_on_and_puts_everything_back():
  """Detection is started on the channel, the arm stepped 0.1 mm at a time to the end at the probe speed, all undone."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    channel = p.pipettes.channels[1]
    assert (channel.calibration, channel.clld) == (Address(1, 238, 512), Address(1, 238, 261))
    here = (await p.pipettes.request_locations())[1].x
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="detected nothing between"):
      await p.pipettes.probe_x_using_clld(1, "left", end_pos_search=here - 0.5, speed=5.0)
    start = next(c for c in sent if isinstance(c, PrepCmd.PrepChannelStartCLldDetection))
    assert (start.dest, start.detect_mode, start.sensitivity) == (
      channel.calibration,
      2,
      p.pipettes.default_clld_sensitivity,
    )
    moves = [c.position for c in sent if isinstance(c, PrepCmd.PrepXAxisMoveAbsolute)]
    assert moves == pytest.approx([here - 0.1 * i for i in range(1, 6)])
    assert sum(isinstance(c, PrepCmd.PrepCLldGetStatus) for c in sent) == 5
    assert sum(isinstance(c, PrepCmd.PrepChannelStartCLldDetection) for c in sent) == 1
    stop = next(c for c in sent if isinstance(c, PrepCmd.PrepChannelStopCLldDetection))
    assert stop.dest == channel.calibration
    assert [c.value for c in sent if isinstance(c, PrepCmd.PrepXAxisSetVelocity)] == [
      5.0,
      SIMULATED_X_VELOCITY,
    ]
    assert await p.x_arm.request_position() == pytest.approx(here - 0.5)
    await p.stop()

  _run(_t())


def test_probe_x_using_clld_stops_at_the_first_detection_and_backs_off():
  """The step after which the channel reports a detection ends the only search; the surface is half the tip further."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.x_arm is not None
    here = (await p.pipettes.request_locations())[1].x
    polls = 0
    sent: list = []
    send = p.send_command

    async def detect_on_third_read(command, *args, **kwargs):
      nonlocal polls
      sent.append(command)
      if isinstance(command, PrepCmd.PrepCLldGetStatus):
        polls += 1
        if polls == 3:
          return PrepCmd.PrepCLldGetStatus.Response(
            detected=[True], detect_index=[10], length=[20], sample_rate=1
          )
      return await send(command, *args, **kwargs)

    p.send_command = detect_on_third_read  # type: ignore[method-assign]
    surface = await p.pipettes.probe_x_using_clld(1, "left", end_pos_search=here - 5.0)
    assert polls == 3
    moves = [c.position for c in sent if isinstance(c, PrepCmd.PrepXAxisMoveAbsolute)]
    assert moves[:3] == pytest.approx([here - 0.1, here - 0.2, here - 0.3])
    assert sum(isinstance(c, PrepCmd.PrepChannelStartCLldDetection) for c in sent) == 1
    assert sum(isinstance(c, PrepCmd.PrepChannelStopCLldDetection) for c in sent) == 1
    assert surface == pytest.approx(here - 0.3 - 0.6)
    assert await p.x_arm.request_position() == pytest.approx(here - 0.3 + 2.0)
    await p.stop()

  _run(_t())


def test_probe_x_using_clld_refuses_searches_it_cannot_make():
  """Unknown direction, no speed, backwards, or no such channel: refused before anything moves."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    here = (await p.pipettes.request_locations())[1].x
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    probe = p.pipettes.probe_x_using_clld
    for call, message in (
      (lambda: probe(1, "up", end_pos_search=here - 1.0), "probing_direction"),  # type: ignore[arg-type]
      (lambda: probe(1, "left", end_pos_search=here - 1.0, speed=0), "speed must be above 0"),
      (lambda: probe(1, "left", end_pos_search=here + 1.0), "cannot end at"),
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


def test_probe_z_using_clld_seeks_where_the_channel_stands():
  """The seek is sent at the channel's current X and Y with every other argument written out."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    before = (await p.pipettes.request_locations())[1]
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    found = await p.pipettes.probe_z_using_clld(1, start_pos_search=160.0, lowest_immers_pos=100.0)
    assert found is None  # nothing to detect in simulation
    seeks = [c for c in sent if type(c).__name__ == "PrepZSeekLldPosition"]
    assert len(seeks) == 1
    seek = seeks[0].seek_parameters[0]
    assert int(seek.channel) == int(PrepCmd.ChannelIndex.FrontChannel)
    assert (seek.seek_position_x, seek.seek_position_y) == pytest.approx(
      (before.x, before.y), abs=1e-3
    )
    assert (seek.seek_height, seek.min_seek_height, seek.final_position_z) == (160.0, 100.0, 160.0)
    assert seek.seek_velocity_z == p.pipettes.default_clld_probe_speed
    assert (seek.lld_sensitivity, seek.detect_mode) == (1, 0)
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
    with pytest.raises(ValueError, match="above start_pos_search"):
      await p.pipettes.probe_z_using_clld(0, start_pos_search=120.0, lowest_immers_pos=130.0)
    with pytest.raises(ValueError, match="lowest_immers_pos=10.0 outside channel 0"):
      await p.pipettes.probe_z_using_clld(0, start_pos_search=160.0, lowest_immers_pos=10.0)
    with pytest.raises(ValueError, match="channel_idx must be between"):
      await p.pipettes.probe_z_using_clld(5, start_pos_search=160.0, lowest_immers_pos=100.0)
    await p.stop()

  _run(_t())


def test_x_arm_move_sets_speed_and_acceleration_for_the_axis_move_and_puts_them_back():
  """The X axis move runs at the given (or default) speed and acceleration, restored afterwards."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    await p.pipettes.move_to_safe_z()
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.x_arm.move_to_x_position(200.0)
    axis = [c for c in sent if type(c).__name__.startswith("PrepXAxis")]
    assert [type(c).__name__ for c in axis] == [
      "PrepXAxisGetCommandedPosition",
      "PrepXAxisGetVelocity",
      "PrepXAxisGetAcceleration",
      "PrepXAxisSetVelocity",
      "PrepXAxisSetAcceleration",
      "PrepXAxisMoveAbsolute",
      "PrepXAxisSetVelocity",
      "PrepXAxisSetAcceleration",
    ]
    assert (axis[3].value, axis[4].value) == (320.0, 1800.0)
    assert axis[5].position == 200.0
    assert (axis[6].value, axis[7].value) == (400.0, 2250.0)
    assert await p.x_arm.request_position() == 200.0

    sent.clear()
    await p.x_arm.move_to_x_position(150.0, speed=100.0, acceleration=500.0)
    values = [
      c.value
      for c in sent
      if type(c).__name__ in ("PrepXAxisSetVelocity", "PrepXAxisSetAcceleration")
    ]
    assert values[:2] == [100.0, 500.0]
    await p.stop()

  _run(_t())


def test_x_arm_move_leaves_lowered_channels_where_they_are():
  """An X axis move neither raises the channels nor refuses while one is lowered."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 100.0), use_channels=1)
    z_before = [position.z for position in await p.pipettes.request_locations()]
    await p.x_arm.move_to_x_position(200.0)
    assert await p.x_arm.request_position() == 200.0
    assert [position.z for position in await p.pipettes.request_locations()] == z_before
    with pytest.raises(ValueError, match="speed must be above 0"):
      await p.x_arm.move_to_x_position(200.0, speed=500.0)
    await p.stop()

  _run(_t())


def test_x_arm_probe_home_flag_seeks_at_the_given_speed_and_returns_the_deck_frame():
  """The seek is sent with its arguments written out, at a set velocity that is put back."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    await p.pipettes.move_to_safe_z()
    start = await p.x_arm.request_position()
    assert start is not None
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    tripped = await p.x_arm.probe_home_flag(-30.0)
    assert tripped == pytest.approx(start, abs=1e-3)  # no flag is modelled in simulation
    axis = [c for c in sent if type(c).__name__.startswith("PrepXAxis")]
    assert [type(c).__name__ for c in axis] == [
      "PrepXAxisGetCommandedPosition",
      "PrepXAxisGetVelocity",
      "PrepXAxisSetVelocity",
      "PrepXAxisSeekToHomeFlag",
      "PrepXAxisSetVelocity",
    ]
    seek = axis[3]
    assert (seek.distance, seek.travel_limits_enable, seek.trip_sense) == (-30.0, True, 0)
    assert (axis[2].value, axis[4].value) == (100.0, 400.0)

    with pytest.raises(ValueError, match="outside the channels' range"):
      p.pipettes.configuration.channels[0].x_range = (0.0, 299.0)
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
    sent: list = []
    execute = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.pipettes.probe_z_using_clld(1, start_pos_search=160.0)
    seek = next(c for c in sent if type(c).__name__ == "PrepZSeekLldPosition").seek_parameters[0]
    assert seek.min_seek_height == pytest.approx(18.03)

    p.pipettes.configuration.channels[1].z_range = None
    with pytest.raises(RuntimeError, match="Z range has not been read"):
      await p.pipettes.probe_z_using_clld(1, start_pos_search=160.0)
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


def test_moves_refuse_channels_too_close_or_out_of_order():
  """Neither move sends anything that puts neighbouring channels closer than their spacing, or swaps them."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="at least 9.0 mm apart"):
      await p.pipettes.move_to_y_positions({0: 100.0, 1: 95.0})
    with pytest.raises(ValueError, match="at least 9.0 mm apart"):
      await p.pipettes.move_to_y_positions({0: 100.0, 1: 120.0})
    here = await p.pipettes.request_locations()
    with pytest.raises(ValueError, match="at least 9.0 mm apart"):
      # The front channel sent to 5 mm in front of the rear one, which stays where it is.
      await p.pipettes.move_to_location(
        Coordinate(here[1].x, here[0].y - 5.0, here[1].z), use_channels=1
      )
    assert "PrepMoveToPosition" not in sent
    assert "PrepMoveYAbsolute" not in sent
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
    here = await p.pipettes.request_locations()
    with pytest.raises(
      ValueError, match=r"Channel 0 was not named, so it stays at y=200\.00 mm\.$"
    ):
      await p.pipettes.move_to_location(Coordinate(here[1].x, 195.0, here[1].z), use_channels=1)
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
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(type(command).__name__)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
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
