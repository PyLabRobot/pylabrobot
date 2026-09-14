"""PipetteChannel facade + enumeration against the simulator."""

from __future__ import annotations

import asyncio

import pytest

from pylabrobot.hamilton.prep import PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.pipettes import PipetteChannel, Pipettes
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
      Coordinate(150.0, 200.0, 160.0), use_channels=0, x_speed_scale=25, z_speed_scale=50
    )
    names = [type(c).__name__ for c in sent]
    move = names.index("PrepMoveToPosition")
    assert names[:move] == [
      "PrepGetXSpeedScale",
      "PrepSetXSpeedScale",
      "PrepGetZSpeedScale",
      "PrepSetZSpeedScale",
    ]
    assert (sent[1].value, sent[3].value) == (25, 50)
    assert names[move + 1 : move + 3] == ["PrepSetXSpeedScale", "PrepSetZSpeedScale"]
    assert (sent[move + 1].value, sent[move + 2].value) == (100, 100)

    with pytest.raises(ValueError, match="between 1 and 100"):
      await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 160.0), x_speed_scale=0)
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
    await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 160.0), x_speed=150.0)
    scales = [c.value for c in sent if type(c).__name__ == "PrepSetXSpeedScale"]
    assert scales == [25, 100]
    with pytest.raises(ValueError, match="not both"):
      await p.pipettes.move_to_location(
        Coordinate(150.0, 200.0, 160.0), x_speed=150.0, x_speed_scale=25
      )
    with pytest.raises(ValueError, match="between 6.0 and 400.0 mm/s"):
      await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 160.0), x_speed=500.0)
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
    await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 160.0))
    p.pipettes.default_x_speed = 60.0
    await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 160.0))
    scales = [c.value for c in sent if type(c).__name__ == "PrepSetXSpeedScale"]
    assert scales == [53, 100, 10, 100]
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


def test_x_arm_move_refuses_with_a_channel_below_the_traverse_height():
  """An X axis move does not raise the channels, so it is refused while one is lowered."""

  async def _t():
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.x_arm is not None and p.pipettes is not None
    await p.pipettes.move_to_location(Coordinate(150.0, 200.0, 100.0), use_channels=1)
    with pytest.raises(RuntimeError, match="below the traverse height"):
      await p.x_arm.move_to_x_position(200.0)
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
