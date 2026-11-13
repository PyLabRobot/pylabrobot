"""The grids a resource lays its positions on, as the page is given them to draw."""

import unittest

from pylabrobot.resources.hamilton import STARLetDeck, hamilton_plate_carrier_L5_ac
from pylabrobot.visualizer3D.grids import describe_grid


class DescribeGridTests(unittest.TestCase):
  """A deck's grid is measured in the deck's own frame, the frame its marks are drawn in."""

  def test_a_turned_deck_has_the_grid_it_has_upright(self):
    """The page draws a deck's marks in the deck's own frame and turns them with the deck, so the
    grid is in that frame too. From a STARlet's front edge, its marks end at its back, 653.5 mm,
    or at the back of a carrier on track 1, 560 mm: an `extent` of 590.5 or 497 mm from the first
    track's line at 63 mm. A STARlet turned 90 degrees and measured along the world's y would end
    its marks 1005 mm from its front edge, or 198 mm beside the carrier."""
    for turn in (90, 37.5):
      for with_carrier, extent in ((False, 590.5), (True, 497.0)):
        upright, turned = STARLetDeck(), STARLetDeck()
        if with_carrier:
          for deck in (upright, turned):
            deck.assign_child_resource(hamilton_plate_carrier_L5_ac("carrier"), track=1)
        turned.rotate(z=turn)
        with self.subTest(turn=turn, with_carrier=with_carrier):
          upright_grid, turned_grid = describe_grid(upright), describe_grid(turned)
          assert upright_grid is not None and turned_grid is not None
          self.assertAlmostEqual(upright_grid.pop("extent"), extent, places=3)
          self.assertAlmostEqual(turned_grid.pop("extent"), extent, places=3)
          self.assertEqual(turned_grid, upright_grid)
