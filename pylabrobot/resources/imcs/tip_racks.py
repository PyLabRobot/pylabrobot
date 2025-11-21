from pylabrobot.resources.hamilton.tip_creators import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.utils import create_ordered_items_2d


def imcs_tip() -> HamiltonTip:
  """IMCS tip. Same as "Hamilton Standard volume (300 µL) tip without a filter", but tips are 6mm shorter."""
  return HamiltonTip(
    has_filter=True,
    total_tip_length=59.9 - 6,  # - 6
    maximal_volume=360,
    tip_size=TipSize.STANDARD_VOLUME,
    # requires "full_blowout" pickup method from STARBackend.pick_up_tips96. Not available on pip channels.
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def imcs_96_tiprack_300uL_filter(name: str, with_tips: bool = True) -> TipRack:
  """Tip racks for IMCS tips. Same as Hamilton 300 µL filter tip racks, but tips are 6mm shorter.

  requires `"full_blowout"` pickup method from STARBackend.pick_up_tips96.
  Not available on pip channels...

  Part numbers:
  - 04T-H8R80A-1-5-96
  - 04T-H8R80A-1-10-96
  - 04T-H8R80P-1-5-96
  - 04T-H8R80P-1-10-96
  - 04T-H8R72-1-2-96
  - 04T-H8R72-1-5-96
  - 04T-H8R72-1-10-96
  - 04T-H8R72Q-1-10-96
  - 04T-H8R85P-1-5-96
  - 04T-H8R85P-1-10-96
  - 04T-H8R88F-1-2-96
  - 04T-H8R88F-1-5-96
  - 04T-H8R88F-1-10-96
  - 04T-H8R89-1-10-96
  - 04T-H8D20F-1A-3-96
  - 04T-H8CD20F-1A-3-96
  - 04T-H8R68-1-5-96
  - 04T-H8R73-1-10-96
  - 04T-H8R05-1-2-96
  - 04T-H8R05-1-5-96
  - 04T-H8R41-1-2-96
  - 04T-H8R53-1-2-96
  - 04T-H8R52-1-2-96
  - 04T-H8R52-1-5-96
  - 04T-H8R30-1-2-96
  - 04T-H8R30-1-5-96
  - 04T-H8R03R-1-5-96
  - 04T-H8R02R-1-2-96
  - 04T-H8R02R-1-5-96
  - 04T-I3R73-1-10-96
  """

  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=imcs_96_tiprack_300uL_filter.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-50.5 + 6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=imcs_tip,
    ),
    with_tips=with_tips,
  )
