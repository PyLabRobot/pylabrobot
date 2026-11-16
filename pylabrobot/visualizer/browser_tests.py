"""The 2D page in a real browser: what Python serializes for a deck is what the page draws.

Skipped where there is no Chrome to drive, so it is a no-op on a computer without one.
"""

import unittest

from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.visualizer import Visualizer
from pylabrobot.visualizer3D.browser_tests import CHROME, Browser


@unittest.skipUnless(CHROME, "no headless browser to drive")
class HamiltonDeckBrowserTests(unittest.IsolatedAsyncioTestCase):
  """A Hamilton deck, served by a real `Visualizer` to a real page."""

  async def test_a_star_deck_draws_its_tracks(self) -> None:
    """One line per track plus one closing the last, numbered, and shown in the attribute panel."""
    vis = Visualizer(STARDeck(), ws_port=0, fs_port=0, open_browser=False)
    await vis.setup()
    self.addAsyncCleanup(vis.stop)
    async with Browser() as browser:
      await browser.open(f"http://{vis.host}:{vis.fs_port}/")
      try:
        await browser.settle("document.readyState === 'complete'", 30)
      except AssertionError:
        self.skipTest("a CDN the page loads from has not answered after 30 s")
      if not await browser.evaluate("typeof Konva !== 'undefined'"):
        self.skipTest("the page loads Konva from a CDN, which cannot be reached from here")
      await browser.settle("getDeck()?.mainShape !== undefined", 30)
      labels = await browser.evaluate(
        "getDeck().mainShape.find('Text').map((t) => Number(t.text()))"
      )
      self.assertEqual(labels, [1, *range(5, 55, 5)])
      self.assertEqual(await browser.evaluate("getDeck().mainShape.find('Line').length"), 55)
      panel = await browser.evaluate("getUmlAttributes(getDeck())")
      self.assertIn({"key": "num_tracks", "value": 54}, panel)
