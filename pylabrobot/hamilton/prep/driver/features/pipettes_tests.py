"""PipetteChannel facade + enumeration against the chatterbox."""

from __future__ import annotations

import asyncio

import pytest

from pylabrobot.hamilton.prep import PrepDriver
from pylabrobot.hamilton.prep.driver.features.pipettes import Pipettes, PipetteChannel
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import PrepDeck, STARLetDeck, hamilton_96_tiprack_50uL_NTR
from pylabrobot.resources.tip_tracker import set_tip_tracking
from pylabrobot.resources.volume_tracker import set_volume_tracking


def _run(coro):
  asyncio.run(coro)


def test_channels_match_configuration_num_channels():
  """Pipettes.channels length matches the configuration's num_channels on a default chatterbox."""

  async def _t():
    p = PrepDriver(deck=STARLetDeck(), chatterbox=True)
    await p.setup()
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    assert len(p.pipettes.channels) == p.configuration.num_channels
    for i, ch in enumerate(p.pipettes.channels):
      assert isinstance(ch, PipetteChannel)
      assert ch.index == i
    await p.stop()

  _run(_t())


def test_channels_attach_bounds_even_when_empty_offline():
  """Chatterbox firmware tree is empty, so bounds are None — but the attribute must exist."""

  async def _t():
    p = PrepDriver(deck=STARLetDeck(), chatterbox=True)
    await p.setup()
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    for ch in p.pipettes.channels:
      assert ch.bounds is None
    await p.stop()

  _run(_t())


def test_channels_tip_trackers_pick_and_drop():
  """pick_up_tips / drop_tips update spot + channel TipTrackers when tip tracking is on."""

  async def _t():
    set_tip_tracking(True)
    try:
      deck = PrepDeck()
      tip_rack = deck[3] = hamilton_96_tiprack_50uL_NTR(name="ntr", with_tips=True)
      p = PrepDriver(deck=deck, chatterbox=True)
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
      p = PrepDriver(deck=deck, chatterbox=True)
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
    p = PrepDriver(deck=STARLetDeck(), chatterbox=True)
    await p.setup(default_traverse_height=150.0)
    assert p.pipettes is not None
    c = p.pipettes.configuration
    assert len(c.channels) == p.num_channels
    assert p.pipettes.default_minimum_traverse_height == 150.0
    assert c.use_v1_aspirate_dispense is False
    assert c.supports_v2_pipetting is True
    await p.stop()

  _run(_t())


def test_move_to_coordinate_refuses_what_the_channel_bounds_exclude():
  """The reach recorded on a channel's configuration guards the move before anything is sent."""

  async def _t():
    p = PrepDriver(deck=STARLetDeck(), chatterbox=True)
    await p.setup()
    assert p.pipettes is not None
    c = p.pipettes.configuration.channels[0]
    c.x_range, c.y_range, c.z_range = (0.0, 400.0), (0.0, 400.0), (0.0, 170.0)
    with pytest.raises(ValueError, match="outside channel 0"):
      await p.pipettes.move_to_coordinate(Coordinate(500.0, 100.0, 100.0), use_channels=0)
    with pytest.raises(ValueError, match="above channel 0 maximum"):
      await p.pipettes.move_to_coordinate(Coordinate(100.0, 100.0, 200.0), use_channels=0)
    with pytest.raises(ValueError, match="same x"):
      await p.pipettes.move_to_coordinate(
        [Coordinate(100.0, 200.0, 100.0), Coordinate(101.0, 100.0, 100.0)], use_channels=[0, 1]
      )
    await p.stop()

  _run(_t())


def test_move_to_coordinate_keeps_each_location_with_its_channel():
  """Locations named out of channel order still go to the channels they were named for."""

  async def _t():
    p = PrepDriver(deck=PrepDeck(), chatterbox=True)
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_to_coordinate(
      [Coordinate(150.0, 200.0, 160.0), Coordinate(150.0, 250.0, 165.0)], use_channels=[1, 0]
    )
    positions = await p.pipettes.request_channel_positions()
    assert (positions[0].y, positions[0].z) == (250.0, 165.0)
    assert (positions[1].y, positions[1].z) == (200.0, 160.0)
    assert positions[0].x == positions[1].x == 150.0
    await p.stop()

  _run(_t())


def test_move_to_coordinate_sets_speed_scales_for_the_move_and_puts_them_back():
  """A speed scale is read, set, the move sent, and the earlier scale restored - in that order."""

  async def _t():
    p = PrepDriver(deck=PrepDeck(), chatterbox=True)
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    execute = p.client.execute

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.client.execute = record  # type: ignore[method-assign]
    await p.pipettes.move_to_coordinate(
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
      await p.pipettes.move_to_coordinate(Coordinate(150.0, 200.0, 160.0), x_speed_scale=0)
    await p.stop()

  _run(_t())


def test_move_to_coordinate_x_speed_is_set_as_the_nearest_scale():
  """x_speed in mm/s becomes the X speed scale; it and x_speed_scale cannot both be given."""

  async def _t():
    p = PrepDriver(deck=PrepDeck(), chatterbox=True)
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    execute = p.client.execute

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.client.execute = record  # type: ignore[method-assign]
    await p.pipettes.move_to_coordinate(Coordinate(150.0, 200.0, 160.0), x_speed=150.0)
    scales = [c.value for c in sent if type(c).__name__ == "PrepSetXSpeedScale"]
    assert scales == [25, 100]
    with pytest.raises(ValueError, match="not both"):
      await p.pipettes.move_to_coordinate(
        Coordinate(150.0, 200.0, 160.0), x_speed=150.0, x_speed_scale=25
      )
    with pytest.raises(ValueError, match="between 6.0 and 400.0 mm/s"):
      await p.pipettes.move_to_coordinate(Coordinate(150.0, 200.0, 160.0), x_speed=500.0)
    await p.stop()

  _run(_t())


def test_move_to_coordinate_uses_default_x_speed_when_none_is_given():
  """With no speed named, the move is sent at `default_x_speed`, and a changed default is honoured."""

  async def _t():
    p = PrepDriver(deck=PrepDeck(), chatterbox=True)
    await p.setup()
    assert p.pipettes is not None
    sent: list = []
    execute = p.client.execute

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await execute(command, *args, **kwargs)

    p.client.execute = record  # type: ignore[method-assign]
    await p.pipettes.move_to_coordinate(Coordinate(150.0, 200.0, 160.0))
    p.pipettes.default_x_speed = 60.0
    await p.pipettes.move_to_coordinate(Coordinate(150.0, 200.0, 160.0))
    scales = [c.value for c in sent if type(c).__name__ == "PrepSetXSpeedScale"]
    assert scales == [53, 100, 10, 100]
    await p.stop()

  _run(_t())
