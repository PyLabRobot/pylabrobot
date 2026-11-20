"""Plates and reservoirs on Biomek's labware classes (cm there, mm here).

- `xspan`, `yspan`, `height`: the footprint and height; `wellsx` / `wellsy`, `wellxspacing` /
  `wellyspacing`, `well1xoffset` / `well1yoffset` (A1's centre from the left and back edges): the
  grid; `welldepth`, the top segment's `topx` / `topy`: a well's size.
- well sections from the bottom up become each well's `height_volume_data`.
- a reservoir's `sections`: its wells, `x1..x2`, `y1..y2` from the left and back edges, `depth`.
- `gripperinfo`: where Biomek's gripper pads go (`preferred_pickup_location`), the fingertips
  `gripperzoffset` above the bottom (below it for a plate).
- `stackablelabwareclasses`: how deep a lid sits on it.
"""

from typing import Dict, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import CrossSectionType, Well, WellBottomType

from .biomek_resource import _WellSegment, height_volume_data_from_segments

_96_WELLPLATE_VB_SEGMENTS = (
  _WellSegment(2.0, 2.735, 2.735, 0.0, 0.0),
  _WellSegment(8.5, 4.0, 4.0, 4.0, 4.0),
)
_96_WELLPLATE_VB_DEPTH = 10.5
_96_WELLPLATE_VB_HVD = height_volume_data_from_segments(
  _96_WELLPLATE_VB_SEGMENTS, _96_WELLPLATE_VB_DEPTH
)

_96_WELLPLATE_FLAT_SEGMENTS = (_WellSegment(10.668, 3.43, 3.43, 3.175, 3.175),)
_96_WELLPLATE_FLAT_DEPTH = 10.668
_96_WELLPLATE_FLAT_HVD = height_volume_data_from_segments(
  _96_WELLPLATE_FLAT_SEGMENTS, _96_WELLPLATE_FLAT_DEPTH
)

_96_DEEPWELL_SQUARE_SEGMENTS = (
  _WellSegment(8.61, 3.85, 3.85, 0.0, 0.0),
  _WellSegment(33.3, 4.14, 4.14, 4.14, 4.14, round=False),
)
_96_DEEPWELL_SQUARE_DEPTH = 41.91
_96_DEEPWELL_SQUARE_HVD = height_volume_data_from_segments(
  _96_DEEPWELL_SQUARE_SEGMENTS, _96_DEEPWELL_SQUARE_DEPTH
)

_24_WELLPLATE_SQUARE_RB_SEGMENTS = (
  _WellSegment(8.0, 8.0, 8.0, 0.0, 0.0, cap=True),
  _WellSegment(32.0, 8.5, 8.5, 8.5, 8.5, round=False),
)
_24_WELLPLATE_SQUARE_RB_DEPTH = 40.0
_24_WELLPLATE_SQUARE_RB_HVD = height_volume_data_from_segments(
  _24_WELLPLATE_SQUARE_RB_SEGMENTS, _24_WELLPLATE_SQUARE_RB_DEPTH
)

_RESERVOIR_350CB_SEGMENTS = (_WellSegment(37.4, 61.9, 40.75, 61.9, 40.75, round=False),)
_RESERVOIR_350CB_DEPTH = 37.4
_RESERVOIR_350CB_HVD = height_volume_data_from_segments(
  _RESERVOIR_350CB_SEGMENTS, _RESERVOIR_350CB_DEPTH
)

_RESERVOIR_SEGMENTS = (_WellSegment(14.53, 55.52, 36.325, 55.52, 36.325, round=False),)
_RESERVOIR_DEPTH = 14.53
_RESERVOIR_HVD = height_volume_data_from_segments(_RESERVOIR_SEGMENTS, _RESERVOIR_DEPTH)


def beckman_coulter_biomek_96_wellplate_vb(name: str, with_lid: bool = False) -> Plate:
  """Greiner polystyrene microplate with 96 round, V-bottomed wells. Part no. 651XXX.

  Biomek labware class Thermo96VB_PP."""
  lid: Optional[Lid] = None
  if with_lid:
    # PyLabRobot's lid covers the plate's footprint; Biomek seats it
    # (stackablelabwareclasses TiterplateLid: x 0.04, y 0.152, z 0.8 cm) 6.1 mm into the plate.
    lid = Lid(
      name=f"{name}_lid",
      size_x=127.8,
      size_y=85.6,
      size_z=10.0,  # TiterplateLid height 1.0 cm
      nesting_z_height=6.1,
      model="beckman_coulter_biomek_lid_titerplatelid",
    )
    # Biomek's pads: the TiterplateLid's own centre on its seat, gripperzoffset above its bottom.
    lid.preferred_pickup_location = Coordinate(63.9, 42.805, 2.0)
  plate = Plate(
    name=name,
    size_x=127.8,  # xspan 12.78 cm
    size_y=85.6,  # yspan 8.56 cm
    size_z=14.1,  # height 1.41 cm
    model=beckman_coulter_biomek_96_wellplate_vb.__name__,
    lid=lid,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.31,  # well1xoffset 1.44 cm, less half the well
      dy=8.31,  # the front row; A1 (well1yoffset 1.12 cm) is at the back
      dz=3.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.18,
      size_y=6.18,
      size_z=_96_WELLPLATE_VB_DEPTH,  # welldepth 1.05 cm
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      height_volume_data=_96_WELLPLATE_VB_HVD,
      max_volume=440.0,  # wellvolume
      name_prefix=name,
    ),
  )
  plate.preferred_pickup_location = Coordinate(63.9, 42.8, -2.3)
  return plate


def beckman_coulter_biomek_96_wellplate_flat(name: str, with_lid: bool = False) -> Plate:
  """Beckman Coulter microplate with 96 round, flat-bottomed wells. Part no. 609844.

  Biomek labware class BCFlat96."""
  lid: Optional[Lid] = None
  if with_lid:
    # PyLabRobot's lid covers the plate's footprint; Biomek seats it
    # (stackablelabwareclasses TiterplateLid: x 0.038, y 0.146, z 0.8 cm) 6.224 mm into the plate.
    lid = Lid(
      name=f"{name}_lid",
      size_x=127.762,
      size_y=85.471,
      size_z=10.0,  # TiterplateLid height 1.0 cm
      nesting_z_height=6.224,
      model="beckman_coulter_biomek_lid_titerplatelid",
    )
    # Biomek's pads: the TiterplateLid's own centre on its seat, gripperzoffset above its bottom.
    lid.preferred_pickup_location = Coordinate(63.88, 42.736, 2.0)
  plate = Plate(
    name=name,
    size_x=127.762,  # xspan 12.7762 cm
    size_y=85.471,  # yspan 8.5471 cm
    size_z=14.224,  # height 1.4224 cm
    model=beckman_coulter_biomek_96_wellplate_flat.__name__,
    lid=lid,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.95,  # well1xoffset 1.438 cm, less half the well
      dy=7.811,  # the front row; A1 (well1yoffset 1.123 cm) is at the back
      dz=3.556,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.86,
      size_y=6.86,
      size_z=_96_WELLPLATE_FLAT_DEPTH,  # welldepth 1.0668 cm
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      height_volume_data=_96_WELLPLATE_FLAT_HVD,
      max_volume=362.76,  # wellvolume
      name_prefix=name,
    ),
  )
  plate.preferred_pickup_location = Coordinate(63.881, 42.7355, -2.3)
  return plate


def beckman_coulter_biomek_96_deepwell_square(name: str) -> Plate:
  """Beckman Coulter microplate with 96 deep, square, V-bottomed wells. Part no. 140504.

  Biomek labware class BCDeep96Square."""
  plate = Plate(
    name=name,
    size_x=128.17,  # xspan 12.817 cm
    size_y=85.98,  # yspan 8.598 cm
    size_z=44.2,  # height 4.42 cm
    model=beckman_coulter_biomek_96_deepwell_square.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.2364,  # well1xoffset 1.43764 cm, less half the well
      dy=7.46,  # the front row; A1 (well1yoffset 1.138 cm) is at the back
      dz=2.29,
      item_dx=9.0,
      item_dy=9.0,
      size_x=8.28,
      size_y=8.28,
      size_z=_96_DEEPWELL_SQUARE_DEPTH,  # welldepth 4.191 cm
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.RECTANGLE,
      height_volume_data=_96_DEEPWELL_SQUARE_HVD,
      max_volume=2200.0,  # wellvolume
      name_prefix=name,
    ),
  )
  plate.preferred_pickup_location = Coordinate(64.085, 42.99, -2.3)
  return plate


def beckman_coulter_biomek_24_wellplate_square_rb(name: str, with_lid: bool = False) -> Plate:
  """Thomson microplate with 24 square, round-bottomed wells. Part no. 931568.

  Biomek labware class Thomson_24w_Square_RB."""
  lid: Optional[Lid] = None
  if with_lid:
    # PyLabRobot's lid covers the plate's footprint; Biomek seats it
    # (stackablelabwareclasses Thomson_24w_Square_RB_Lid: x 0.0, y 0.0, z 3.4 cm) 10.0 mm into the plate.
    lid = Lid(
      name=f"{name}_lid",
      size_x=127.762,
      size_y=85.471,
      size_z=10.0,  # Thomson_24w_Square_RB_Lid height 1.0 cm
      nesting_z_height=10.0,
      model="beckman_coulter_biomek_lid_thomson_24w_square_rb_lid",
    )
    # Biomek's pads: the Thomson_24w_Square_RB_Lid's own centre on its seat, gripperzoffset above its bottom.
    lid.preferred_pickup_location = Coordinate(63.5, 44.196, 8.0)
  plate = Plate(
    name=name,
    size_x=127.762,  # xspan 12.7762 cm
    size_y=85.471,  # yspan 8.5471 cm
    size_z=44.0,  # height 4.4 cm
    model=beckman_coulter_biomek_24_wellplate_square_rb.__name__,
    lid=lid,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=6,
      num_items_y=4,
      dx=10.5,  # well1xoffset 1.9 cm, less half the well
      dy=6.971,  # the front row; A1 (well1yoffset 1.6 cm) is at the back
      dz=4.0,
      item_dx=18.0,
      item_dy=18.0,
      size_x=17.0,
      size_y=17.0,
      size_z=_24_WELLPLATE_SQUARE_RB_DEPTH,  # welldepth 4.0 cm
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.RECTANGLE,
      height_volume_data=_24_WELLPLATE_SQUARE_RB_HVD,
      max_volume=10000.0,  # wellvolume
      name_prefix=name,
    ),
  )
  plate.preferred_pickup_location = Coordinate(63.881, 42.7355, -2.3)
  return plate


def beckman_coulter_biomek_reservoir_350cb(name: str, with_lid: bool = False) -> Plate:
  """Thermo Nunc reservoir with one flat-bottomed section.

  Biomek labware class ThermoNUNC_350CB: a well per section."""
  lid: Optional[Lid] = None
  if with_lid:
    # PyLabRobot's lid covers the plate's footprint; Biomek seats it
    # (stackablelabwareclasses TiterplateLid: x 0.0, y 0.0, z 3.4 cm) 4.0 mm into the plate.
    lid = Lid(
      name=f"{name}_lid",
      size_x=127.8,
      size_y=85.85,
      size_z=10.0,  # TiterplateLid height 1.0 cm
      nesting_z_height=4.0,
      model="beckman_coulter_biomek_lid_titerplatelid",
    )
    # Biomek's pads: the TiterplateLid's own centre on its seat, gripperzoffset above its bottom.
    lid.preferred_pickup_location = Coordinate(63.5, 44.575, 2.0)
  wells: Dict[str, Well] = {}
  wells["A1"] = Well(
    name=f"{name}_A1",
    size_x=123.8,  # x1 0.2 to x2 12.58 cm
    size_y=81.5,  # y1 0.2 to y2 8.35 cm
    size_z=_RESERVOIR_350CB_DEPTH,  # depth 3.74 cm
    bottom_type=WellBottomType.FLAT,
    cross_section_type=CrossSectionType.RECTANGLE,
    height_volume_data=_RESERVOIR_350CB_HVD,
    max_volume=300000.0,  # maxvolume
  )
  wells["A1"].location = Coordinate(2.0, 2.35, 0.6)
  plate = Plate(
    name=name,
    size_x=127.8,  # xspan 12.78 cm
    size_y=85.85,  # yspan 8.585 cm
    size_z=38.0,  # height 3.8 cm
    model=beckman_coulter_biomek_reservoir_350cb.__name__,
    lid=lid,
    ordered_items=wells,
  )
  plate.preferred_pickup_location = Coordinate(63.9, 42.925, -2.0)
  return plate


def beckman_coulter_biomek_reservoir(name: str) -> Plate:
  """Reservoir used with the Multimek. Part no. 148033.

  Biomek labware class Reservoir: a well per section."""
  wells: Dict[str, Well] = {}
  wells["A1"] = Well(
    name=f"{name}_A1",
    size_x=111.04,  # x1 0.826 to x2 11.93 cm
    size_y=72.65,  # y1 0.635 to y2 7.9 cm
    size_z=_RESERVOIR_DEPTH,  # depth 1.453 cm
    bottom_type=WellBottomType.FLAT,
    cross_section_type=CrossSectionType.RECTANGLE,
    height_volume_data=_RESERVOIR_HVD,
    max_volume=110000.0,  # maxvolume
  )
  wells["A1"].location = Coordinate(8.26, 3.55, 14.68)
  plate = Plate(
    name=name,
    size_x=127.0,  # xspan 12.7 cm
    size_y=82.55,  # yspan 8.255 cm
    size_z=29.21,  # height 2.921 cm
    model=beckman_coulter_biomek_reservoir.__name__,
    ordered_items=wells,
  )
  plate.preferred_pickup_location = Coordinate(63.5, 41.275, -2.3)
  return plate


def beckman_coulter_biomek_modular_reservoir(name: str) -> Plate:
  """Beckman Coulter reservoir that may be customized with added sections. Part no. 372795 (Frame); 372790(Quarter); 373792(Quarter Divided by Width); 372788 (Quarter Divided by Length); 372786(Half); 372794(Half Divided by Width).

  Biomek labware class ModularReservoir: a well per section."""
  wells: Dict[str, Well] = {}
  wells["A1"] = Well(
    name=f"{name}_A1",
    size_x=23.65,  # x1 1.105 to x2 3.47 cm
    size_y=69.19,  # y1 0.772 to y2 7.691 cm
    size_z=24.79,  # depth 2.479 cm
    # a V bottom 1.156 cm deep: no outline
    bottom_type=WellBottomType.V,
    cross_section_type=CrossSectionType.RECTANGLE,
    max_volume=40700.0,  # maxvolume
  )
  wells["A1"].location = Coordinate(11.05, 8.69, 14.07)
  wells["A2"] = Well(
    name=f"{name}_A2",
    size_x=23.65,  # x1 3.84825 to x2 6.21325 cm
    size_y=69.19,  # y1 0.772 to y2 7.691 cm
    size_z=24.79,  # depth 2.479 cm
    # a V bottom 1.156 cm deep: no outline
    bottom_type=WellBottomType.V,
    cross_section_type=CrossSectionType.RECTANGLE,
    max_volume=40700.0,  # maxvolume
  )
  wells["A2"].location = Coordinate(38.4825, 8.69, 14.07)
  wells["A3"] = Well(
    name=f"{name}_A3",
    size_x=23.65,  # x1 6.5915 to x2 8.9565 cm
    size_y=69.19,  # y1 0.772 to y2 7.691 cm
    size_z=24.79,  # depth 2.479 cm
    # a V bottom 1.156 cm deep: no outline
    bottom_type=WellBottomType.V,
    cross_section_type=CrossSectionType.RECTANGLE,
    max_volume=40700.0,  # maxvolume
  )
  wells["A3"].location = Coordinate(65.915, 8.69, 14.07)
  wells["A4"] = Well(
    name=f"{name}_A4",
    size_x=23.65,  # x1 9.33475 to x2 11.69975 cm
    size_y=69.19,  # y1 0.772 to y2 7.691 cm
    size_z=24.79,  # depth 2.479 cm
    # a V bottom 1.156 cm deep: no outline
    bottom_type=WellBottomType.V,
    cross_section_type=CrossSectionType.RECTANGLE,
    max_volume=40700.0,  # maxvolume
  )
  wells["A4"].location = Coordinate(93.3475, 8.69, 14.07)
  plate = Plate(
    name=name,
    size_x=127.41,  # xspan 12.741 cm
    size_y=85.6,  # yspan 8.56 cm
    size_z=38.86,  # height 3.886 cm
    model=beckman_coulter_biomek_modular_reservoir.__name__,
    ordered_items=wells,
  )
  plate.preferred_pickup_location = Coordinate(63.705, 42.8, -2.3)
  return plate
