"""Unit test validating center-center coordinates of Hamilton
tip spots across all PLR-integrated Hamilton tip racks.

These tests instantiate Hamilton tip rack models, compute each
rack's representative H1 tip spot position relative to the tip rack origin
and verify that the reported center-center coordinates match
expected reference values.
"""

import unittest

from pylabrobot.resources.carrier import Coordinate
from pylabrobot.resources.hamilton import (
  hamilton_96_tiprack_10uL,
  hamilton_96_tiprack_10uL_filter,
  hamilton_96_tiprack_50uL,
  hamilton_96_tiprack_50uL_filter,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_300uL_filter_slim,
  hamilton_96_tiprack_300uL_filter_ultrawide,
  hamilton_96_tiprack_1000uL,
  hamilton_96_tiprack_1000uL_filter,
  hamilton_96_tiprack_1000uL_filter_ultrawide,
  hamilton_96_tiprack_1000uL_filter_wide,
)
from pylabrobot.resources.tip_rack import TipRack


class HamiltonTipSpotTests(unittest.TestCase):
  def test_tipspot_h1_cc(self):
    """Tests for PLR-integrated Hamilton TipRacks' accurate TipSpot center-center coordinates."""

    def check_tip_spot_h1(tr: TipRack, expect: Coordinate):
      h1_loc = tr.get_item("H1").get_absolute_location("c", "c")
      assert h1_loc.x == expect.x and h1_loc.y == expect.y, f"{h1_loc} != {expect}"

    common_tip_rack_loc = Coordinate(x=11.7, y=9.8, z=-22.5)
    check_tip_spot_h1(hamilton_96_tiprack_10uL_filter("tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_10uL(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_50uL_filter(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_50uL(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_300uL_filter(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_300uL(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_300uL_filter_slim(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_300uL_filter_ultrawide(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_1000uL_filter(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_1000uL(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_1000uL_filter_wide(name="tr"), common_tip_rack_loc)
    check_tip_spot_h1(hamilton_96_tiprack_1000uL_filter_ultrawide(name="tr"), common_tip_rack_loc)

    ntr_loc = Coordinate(x=13.525, y=11.625, z=13.5)
    check_tip_spot_h1(hamilton_96_tiprack_50uL_NTR(name="tr"), ntr_loc)
