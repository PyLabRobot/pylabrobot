"""PrepPIPChannel facade + enumeration against the chatterbox."""

from __future__ import annotations

import asyncio

from pylabrobot.hamilton.prep import Prep
from pylabrobot.hamilton.prep.channels import PrepChannels, PrepPIPChannel
from pylabrobot.resources.hamilton import STARLetDeck


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
