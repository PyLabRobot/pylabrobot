"""PrepPIPChannel facade + enumeration against the chatterbox."""

from __future__ import annotations

import asyncio

import pytest

from pylabrobot.hamilton.prep import Prep
from pylabrobot.hamilton.prep.channels import PrepChannels, PrepPIPChannel
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import PrepDeck, STARLetDeck, hamilton_96_tiprack_50uL_NTR
from pylabrobot.resources.tip_tracker import set_tip_tracking
from pylabrobot.resources.volume_tracker import set_volume_tracking


def _run(coro):
  asyncio.run(coro)


def test_channels_match_info_num_channels():
  """PrepChannels.channels length matches info.config.num_channels on a default chatterbox."""

  async def _t():
    p = Prep(deck=STARLetDeck(), chatterbox=True)
    await p.setup()
    assert p.channels is not None
    assert isinstance(p.channels, PrepChannels)
    assert len(p.channels.channels) == p.info.config.num_channels
    for i, ch in enumerate(p.channels.channels):
      assert isinstance(ch, PrepPIPChannel)
      assert ch.index == i
    await p.stop()

  _run(_t())


def test_channels_attach_bounds_even_when_empty_offline():
  """Chatterbox firmware tree is empty, so bounds are None — but the attribute must exist."""

  async def _t():
    p = Prep(deck=STARLetDeck(), chatterbox=True)
    await p.setup()
    assert p.channels is not None
    assert isinstance(p.channels, PrepChannels)
    for ch in p.channels.channels:
      assert hasattr(ch, "bounds")
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
      p = Prep(deck=deck, chatterbox=True)
      await p.setup()
      assert p.channels is not None
      spots = [tip_rack.get_item("A1"), tip_rack.get_item("B1")]
      n = min(2, p.channels.num_channels)
      spots = spots[:n]
      use = list(range(n))
      assert all(s.has_tip() for s in spots)
      assert all(t is None for t in p.channels.get_mounted_tips()[:n])

      await p.channels.pick_up_tips(spots, use_channels=use)
      assert all(not s.has_tip() for s in spots)
      mounted = p.channels.get_mounted_tips()
      assert all(mounted[i] is not None for i in use)
      assert all(p.channels.head[i].has_tip for i in use)

      await p.channels.drop_tips(spots, use_channels=use)
      assert all(s.has_tip() for s in spots)
      assert all(not p.channels.head[i].has_tip for i in use)
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
      p = Prep(deck=deck, chatterbox=True)
      await p.setup()
      assert p.channels is not None
      n = min(2, p.channels.num_channels)
      spots = [tip_rack.get_item("A1"), tip_rack.get_item("B1")][:n]
      use = list(range(n))
      src = plate["A1:B1"][:n]
      dst = plate["A7:B7"][:n]
      vols = [20.0] * n
      for well in src:
        well.tracker.set_volume(100.0)

      await p.channels.pick_up_tips(spots, use_channels=use)
      await p.channels.aspirate(
        src,
        vols=vols,
        use_channels=use,
        disable_volume_correction=[True] * n,
      )
      for well in src:
        assert well.tracker.get_used_volume() == pytest.approx(80.0)
      for ch in use:
        tip = p.channels.head[ch].get_tip()
        assert tip.tracker.get_used_volume() == pytest.approx(20.0)

      await p.channels.dispense(
        dst,
        vols=vols,
        use_channels=use,
        disable_volume_correction=[True] * n,
      )
      for well in dst:
        assert well.tracker.get_used_volume() == pytest.approx(20.0)
      for ch in use:
        tip = p.channels.head[ch].get_tip()
        assert tip.tracker.get_used_volume() == pytest.approx(0.0)

      await p.channels.drop_tips(spots, use_channels=use)
      await p.stop()
    finally:
      set_tip_tracking(False)
      set_volume_tracking(False)

  _run(_t())
