"""Beckman Coulter Biomek tip boxes, from Biomek's labware classes (cm there, mm here).

- `xspan`, `yspan`, `height`: the box's footprint and height, tips in.
- `wellsx`, `wellsy`, `wellxspacing`, `wellyspacing`: the tip grid.
- `well1xoffset`, `well1yoffset`: tip A1's centre, from the box's left and back edges.
- `wellradius`: the radius of the hole a tip stands in.
- `gripperinfo`: where Biomek's gripper pads go (`preferred_pickup_location`).

The tips' tops are at the box's `height`, where the head's mandrels meet them, so a tip spot sits
on the top face, as on a nested rack.
"""

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.tip_rack import StandingTipRack, TipSpot
from pylabrobot.resources.utils import create_ordered_items_2d

from .tips import (
  beckman_coulter_biomek_tip_1025uL_filter,
  beckman_coulter_biomek_tip_1070uL,
  beckman_coulter_biomek_tip_190uL_filter,
  beckman_coulter_biomek_tip_230uL,
  beckman_coulter_biomek_tip_50uL_filter,
)


def beckman_coulter_biomek_96_tiprack_50uL_filter(
  name: str, with_tips: bool = True
) -> StandingTipRack:
  """Biomek BC50F tip box: 96 T50F tips (`beckman_coulter_biomek_tip_50uL_filter`). Beckman Coulter tip box containing 96 Aerosol Resistant T50 filter tips. Part no. B85888.

  Labware class BC50F."""
  rack = StandingTipRack(
    name=name,
    size_x=127.9,  # xspan 12.79 cm
    size_y=85.4,  # yspan 8.54 cm
    size_z=55.9,  # height 5.59 cm
    model=beckman_coulter_biomek_96_tiprack_50uL_filter.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,  # well1xoffset 1.45 cm, less the hole's radius
      dy=8.4,  # the front row's hole; A1 (well1yoffset 1.12 cm) is at the back
      dz=55.9,
      item_dx=9.0,
      item_dy=9.0,
      size_x=5.6,  # wellradius 0.28 cm
      size_y=5.6,
      make_tip=beckman_coulter_biomek_tip_50uL_filter,
      name_prefix=name,
    ),
    with_tips=with_tips,
  )
  rack.preferred_pickup_location = Coordinate(63.95, 42.7, -2.3)
  return rack


def beckman_coulter_biomek_96_tiprack_190uL_filter(
  name: str, with_tips: bool = True
) -> StandingTipRack:
  """Biomek BC190F tip box: 96 T190F tips (`beckman_coulter_biomek_tip_190uL_filter`). Beckman Coulter tip box containing 96 Aerosol Resistant T190 filter tips. Part no. B85911.

  Labware class BC190F."""
  rack = StandingTipRack(
    name=name,
    size_x=127.9,  # xspan 12.79 cm
    size_y=85.4,  # yspan 8.54 cm
    size_z=55.9,  # height 5.59 cm
    model=beckman_coulter_biomek_96_tiprack_190uL_filter.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,  # well1xoffset 1.45 cm, less the hole's radius
      dy=8.4,  # the front row's hole; A1 (well1yoffset 1.12 cm) is at the back
      dz=55.9,
      item_dx=9.0,
      item_dy=9.0,
      size_x=5.6,  # wellradius 0.28 cm
      size_y=5.6,
      make_tip=beckman_coulter_biomek_tip_190uL_filter,
      name_prefix=name,
    ),
    with_tips=with_tips,
  )
  rack.preferred_pickup_location = Coordinate(63.95, 42.7, -2.3)
  return rack


def beckman_coulter_biomek_96_tiprack_230uL(name: str, with_tips: bool = True) -> StandingTipRack:
  """Biomek BC230 tip box: 96 T230 tips (`beckman_coulter_biomek_tip_230uL`). Beckman Coulter tip box containing 96 T230 tips. Part no. Sterile: B85906, Non-Sterile: B85903.

  Labware class BC230."""
  rack = StandingTipRack(
    name=name,
    size_x=127.9,  # xspan 12.79 cm
    size_y=85.4,  # yspan 8.54 cm
    size_z=55.9,  # height 5.59 cm
    model=beckman_coulter_biomek_96_tiprack_230uL.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,  # well1xoffset 1.45 cm, less the hole's radius
      dy=8.4,  # the front row's hole; A1 (well1yoffset 1.12 cm) is at the back
      dz=55.9,
      item_dx=9.0,
      item_dy=9.0,
      size_x=5.6,  # wellradius 0.28 cm
      size_y=5.6,
      make_tip=beckman_coulter_biomek_tip_230uL,
      name_prefix=name,
    ),
    with_tips=with_tips,
  )
  rack.preferred_pickup_location = Coordinate(63.95, 42.7, -2.3)
  return rack


def beckman_coulter_biomek_96_tiprack_1025uL_filter(
  name: str, with_tips: bool = True
) -> StandingTipRack:
  """Biomek BC1025F tip box: 96 T1025F tips (`beckman_coulter_biomek_tip_1025uL_filter`). Beckman Coulter tip box containing 96 Aerosol Resistant T1025 filtered tips. Part no. B85955.

  Labware class BC1025F."""
  rack = StandingTipRack(
    name=name,
    size_x=127.9,  # xspan 12.79 cm
    size_y=85.4,  # yspan 8.54 cm
    size_z=114.1,  # height 11.41 cm
    model=beckman_coulter_biomek_96_tiprack_1025uL_filter.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=11.6,  # well1xoffset 1.44 cm, less the hole's radius
      dy=8.4,  # the front row's hole; A1 (well1yoffset 1.12 cm) is at the back
      dz=114.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=5.6,  # wellradius 0.28 cm
      size_y=5.6,
      make_tip=beckman_coulter_biomek_tip_1025uL_filter,
      name_prefix=name,
    ),
    with_tips=with_tips,
  )
  rack.preferred_pickup_location = Coordinate(63.95, 42.7, -2.3)
  return rack


def beckman_coulter_biomek_96_tiprack_1070uL(name: str, with_tips: bool = True) -> StandingTipRack:
  """Biomek BC1070 tip box: 96 T1070 tips (`beckman_coulter_biomek_tip_1070uL`). Beckman Coulter tip box containing 96 T1070 tips. Part no. Sterile: B85945, Non-Sterile: B85940.

  Labware class BC1070."""
  rack = StandingTipRack(
    name=name,
    size_x=127.9,  # xspan 12.79 cm
    size_y=85.4,  # yspan 8.54 cm
    size_z=114.1,  # height 11.41 cm
    model=beckman_coulter_biomek_96_tiprack_1070uL.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=11.6,  # well1xoffset 1.44 cm, less the hole's radius
      dy=8.4,  # the front row's hole; A1 (well1yoffset 1.12 cm) is at the back
      dz=114.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=5.6,  # wellradius 0.28 cm
      size_y=5.6,
      make_tip=beckman_coulter_biomek_tip_1070uL,
      name_prefix=name,
    ),
    with_tips=with_tips,
  )
  rack.preferred_pickup_location = Coordinate(63.95, 42.7, -2.3)
  return rack
