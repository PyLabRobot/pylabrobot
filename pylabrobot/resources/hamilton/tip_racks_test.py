"""Unit tests validating center-center coordinates of Hamilton
tip spots across all PLR-integrated Hamilton tip racks.

These tests instantiate Hamilton tip rack models, compute each
rack's representative H1 tip spot position relative to the deck,
and verify that the reported center-center coordinates match
expected reference values.
"""

import textwrap
import unittest

from pylabrobot.resources.hamilton import (
  TIP_CAR_480_A00,
  MFX_CAR_L5_base,
  STARLetDeck,
  # 'Standard' Hamilton TipRacks
  hamilton_96_tiprack_10uL,
  hamilton_96_tiprack_10uL_filter,
  hamilton_96_tiprack_50uL,
  hamilton_96_tiprack_50uL_filter,
  hamilton_96_tiprack_300uL,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_300uL_filter_slim,
  hamilton_96_tiprack_1000uL,
  hamilton_96_tiprack_1000uL_filter,
  hamilton_96_tiprack_1000uL_filter_ultrawide,
  hamilton_96_tiprack_1000uL_filter_wide,
  # NTRs
  hamilton_96_tiprack_50uL_NTR,
)
from pylabrobot.resources.carrier import Coordinate, ResourceHolder

# TODO: replace with official Hamilton NTR ResourceHolder import when integrated in PLR
def diy_mfx_resource_holder_ntr(name: str) -> ResourceHolder: 
    """Self-built ResourceHolder for NTRs, attached onto a MFX Carrier"""
    return ResourceHolder(
        name=name,
        size_x=135.0,
        size_y=94.0,
        size_z=32.9,
        child_location=Coordinate(3.42, 4.26, 13),
        model=diy_mfx_resource_holder_ntr.__name__,
    )

class HamiltonTipSpotTests(unittest.TestCase):
  """Tests for PLR-integrated Hamilton TipRacks' accurate TipSpot center-center coordinates."""

  def build_layout(self):
    """Build a deck layout for testing"""
    deck = STARLetDeck()

    tip_carrier_0 = TIP_CAR_480_A00(name="tip_carrier_0")
    tip_carrier_0[4] = hamilton_96_tiprack_10uL_filter(name="tiprack_10uL_filter")
    tip_carrier_0[3] = hamilton_96_tiprack_10uL(name="tiprack_10uL")
    tip_carrier_0[2] = hamilton_96_tiprack_50uL_filter(name="tiprack_50uL_filter")
    tip_carrier_0[1] = hamilton_96_tiprack_50uL(name="tiprack_50uL")
    deck.assign_child_resource(tip_carrier_0, rails=1)

    tip_carrier_1 = TIP_CAR_480_A00(name="tip_carrier_1")
    tip_carrier_1[4] = hamilton_96_tiprack_300uL_filter(name="tiprack_300uL_filter")
    tip_carrier_1[3] = hamilton_96_tiprack_300uL(name="tiprack_300uL")
    tip_carrier_1[2] = hamilton_96_tiprack_300uL_filter_slim(name="tiprack_300uL_filter_slim")
    tip_carrier_1[1] = hamilton_96_tiprack_1000uL_filter(name="tiprack_1000uL_filter_filter")
    tip_carrier_1[0] = hamilton_96_tiprack_1000uL(name="tiprack_1000uL_filter")
    deck.assign_child_resource(tip_carrier_1, rails=7)

    tip_carrier_2 = TIP_CAR_480_A00(name="tip_carrier_2")
    tip_carrier_2[4] = hamilton_96_tiprack_1000uL_filter_wide(name="tiprack_1000uL_filter_wide")
    tip_carrier_2[3] = hamilton_96_tiprack_1000uL_filter_ultrawide(
      name="tiprack_1000uL_filter_ultrawide"
    )
    deck.assign_child_resource(tip_carrier_2, rails=13)

    # Nested Tip Racks (NTRs) require a ResourceHolder on a MFX Carrier
    diy_mfx_resource_holder_ntr_0 = diy_mfx_resource_holder_ntr(name="diy_mfx_resource_holder_ntr_0")
    diy_mfx_resource_holder_ntr_0.assign_child_resource(hamilton_96_tiprack_50uL_NTR(name="tiprack_50ul_ntr_0"))
    
    mfx_carrier_modules = {4: diy_mfx_resource_holder_ntr_0}
    mfx_carrier_0 = MFX_CAR_L5_base(
      name="mfx_carrier_0",
      modules=mfx_carrier_modules
    )
    deck.assign_child_resource(mfx_carrier_0, rails=19)

    return deck

  def query_decks_tipspot_h1_center_center_top_location(self):
    """Query PLR's Current TipSpot H1 Positions"""

    deck = self.build_layout()

    tiprack_list = [
      tiprack for tiprack in deck.get_all_children() if tiprack.model and "tiprack" in tiprack.name
    ]

    max_name_len = max([len(tiprack.name) for tiprack in tiprack_list])

    tipspot_center_center_summary = ""

    for tiprack in tiprack_list:
      representative_tipspot = tiprack["H1"][0]

      tipspot_location = representative_tipspot.get_location_wrt(
        deck, x="center", y="center", z="top"
      )

      tipspot_center_center_summary += (
        f"{tiprack.name.ljust(max_name_len)} | "
        f"{representative_tipspot.get_identifier()} | "
        f"center_x={tipspot_location.x} | "
        f"center_y={tipspot_location.y}\n"
      )
      # TODO: add f"top_z={tipspot_location.z} once TipSpot has been fixed

    return tipspot_center_center_summary

  def test_tipspot_h1_summary(self):
    self.maxDiff = None
    tispot_h1_summary = self.query_decks_tipspot_h1_center_center_top_location()
    self.assertEqual(
      tispot_h1_summary.strip(),
      textwrap.dedent(
        """tiprack_50uL                    | H1 | center_x=117.9 | center_y=178.8
tiprack_50uL_filter             | H1 | center_x=117.9 | center_y=274.8
tiprack_10uL                    | H1 | center_x=117.9 | center_y=370.8
tiprack_10uL_filter             | H1 | center_x=117.9 | center_y=466.8
tiprack_1000uL_filter           | H1 | center_x=252.9 | center_y=82.8
tiprack_1000uL_filter_filter    | H1 | center_x=252.9 | center_y=178.8
tiprack_300uL_filter_slim       | H1 | center_x=252.9 | center_y=274.8
tiprack_300uL                   | H1 | center_x=252.9 | center_y=370.8
tiprack_300uL_filter            | H1 | center_x=252.9 | center_y=466.8
tiprack_1000uL_filter_ultrawide | H1 | center_x=387.9 | center_y=370.8
tiprack_1000uL_filter_wide      | H1 | center_x=387.9 | center_y=466.8
tiprack_50ul_ntr_0              | H1 | center_x=521.945 | center_y=467.885
        """.strip()
      ),
    )
