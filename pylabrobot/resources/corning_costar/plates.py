""" Corning Costar plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.itemized_resource import create_equally_spaced


def _compute_volume_from_height_Cos_1536_10ul(h: float):
  volume = min(h, 0.25)*min(h, 0.25)*(2.9845 - 1.0472*min(h, 0.25))
  if h <= 5.75:
    volume += (h-0.25)*3.6100
  if h > 6.0:
    raise ValueError(f"Height {h} is too large for Cos_1536_10ul")
  return volume

#: Cos_1536_10ul
def Cos_1536_10ul(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=10.25,
    with_lid=with_lid,
    model="Cos_1536_10ul",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=48,
      num_items_y=32,
      dx=9.5,
      dy=7.0,
      dz=0.5,
      item_dx=2.25,
      item_dy=2.25,
      size_x=2.25,
      size_y=2.25,
      size_z=5.75,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Cos_1536_10ul,
    ),
  )


#: Cos_1536_10ul_L
def Cos_1536_10ul_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_1536_10ul(name=name, with_lid=with_lid)


#: Cos_1536_10ul_P
def Cos_1536_10ul_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_1536_10ul(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_DW(h: float):
  volume = min(h, 1.0)*min(h, 1.0)*(4.3982 - 1.0472*min(h, 1.0))
  if h <= 24.5:
    volume += (h-1.0)*10.0800
  if h > 25.5:
    raise ValueError(f"Height {h} is too large for Cos_384_DW")
  return volume

#: Cos_384_DW
def Cos_384_DW(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=28.0,
    with_lid=with_lid,
    model="Cos_384_DW",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=24.5,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_DW,
    ),
  )


#: Cos_384_DW_L
def Cos_384_DW_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_DW(name=name, with_lid=with_lid)


#: Cos_384_DW_P
def Cos_384_DW_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_DW(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_PCR(h: float):
  volume = min(h, 9.5)*2.8510
  if h > 9.5:
    raise ValueError(f"Height {h} is too large for Cos_384_PCR")
  return volume

#: Cos_384_PCR
def Cos_384_PCR(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=16.0,
    with_lid=with_lid,
    model="Cos_384_PCR",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=9.5,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_PCR,
    ),
  )


#: Cos_384_PCR_L
def Cos_384_PCR_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_PCR(name=name, with_lid=with_lid)


#: Cos_384_PCR_P
def Cos_384_PCR_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_PCR(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_Sq(h: float):
  volume = min(h, 11.56)*12.2500
  if h > 11.56:
    raise ValueError(f"Height {h} is too large for Cos_384_Sq")
  return volume

#: Cos_384_Sq
def Cos_384_Sq(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    with_lid=with_lid,
    model="Cos_384_Sq",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=11.56,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq,
    ),
  )


#: Cos_384_Sq_L
def Cos_384_Sq_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq(name=name, with_lid=with_lid)


#: Cos_384_Sq_P
def Cos_384_Sq_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_Sq_Rd(h: float):
  volume = min(h, 1.0)*min(h, 1.0)*(4.3982 - 1.0472*min(h, 1.0))
  if h <= 11.6:
    volume += (h-1.0)*10.0800
  if h > 12.6:
    raise ValueError(f"Height {h} is too large for Cos_384_Sq_Rd")
  return volume

#: Cos_384_Sq_Rd
def Cos_384_Sq_Rd(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    with_lid=with_lid,
    model="Cos_384_Sq_Rd",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=11.6,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_Rd,
    ),
  )


#: Cos_384_Sq_Rd_L
def Cos_384_Sq_Rd_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq_Rd(name=name, with_lid=with_lid)


#: Cos_384_Sq_Rd_P
def Cos_384_Sq_Rd_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq_Rd(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_DW_1mL(h: float):
  volume = min(h, 2.5)*min(h, 2.5)*(10.2102 - 1.0472*min(h, 2.5))
  if h <= 40.0:
    volume += (h-2.5)*33.1831
  if h > 42.5:
    raise ValueError(f"Height {h} is too large for Cos_96_DW_1mL")
  return volume

#: Cos_96_DW_1mL
def Cos_96_DW_1mL(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=42.0,
    with_lid=with_lid,
    model="Cos_96_DW_1mL",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=40.0,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_1mL,
    ),
  )


#: Cos_96_DW_1mL_L
def Cos_96_DW_1mL_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_1mL(name=name, with_lid=with_lid)


#: Cos_96_DW_1mL_P
def Cos_96_DW_1mL_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_1mL(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_DW_2mL(h: float):
  volume = min(h, 4.0)*min(h, 4.0)*(12.5664 - 1.0472*min(h, 4.0))
  if h <= 42.0:
    volume += (h-4.0)*64.0000
  if h > 46.0:
    raise ValueError(f"Height {h} is too large for Cos_96_DW_2mL")
  return volume

#: Cos_96_DW_2mL
def Cos_96_DW_2mL(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=43.5,
    with_lid=with_lid,
    model="Cos_96_DW_2mL",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=42.0,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_2mL,
    ),
  )


#: Cos_96_DW_2mL_L
def Cos_96_DW_2mL_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_2mL(name=name, with_lid=with_lid)


#: Cos_96_DW_2mL_P
def Cos_96_DW_2mL_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_2mL(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_DW_500ul(h: float):
  volume = min(h, 1.5)*10.7233
  if h <= 25.0:
    volume += (h-1.5)*34.7486
  if h > 26.5:
    raise ValueError(f"Height {h} is too large for Cos_96_DW_500ul")
  return volume

#: Cos_96_DW_500ul
def Cos_96_DW_500ul(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=27.5,
    with_lid=with_lid,
    model="Cos_96_DW_500ul",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=2.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=25.0,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_500ul,
    ),
  )


#: Cos_96_DW_500ul_L
def Cos_96_DW_500ul_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_500ul(name=name, with_lid=with_lid)


#: Cos_96_DW_500ul_P
def Cos_96_DW_500ul_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_500ul(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_EZWash(h: float):
  volume = min(h, 11.3)*37.3928
  if h > 11.3:
    raise ValueError(f"Height {h} is too large for Cos_96_EZWash")
  return volume

#: Cos_96_EZWash
def Cos_96_EZWash(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    with_lid=with_lid,
    model="Cos_96_EZWash",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=11.3,
      bottom_type=WellBottomType.FLAT,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_EZWash,
    ),
  )


#: Cos_96_EZWash_L
def Cos_96_EZWash_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_EZWash(name=name, with_lid=with_lid)


#: Cos_96_EZWash_P
def Cos_96_EZWash_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_EZWash(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_FL(h: float):
  volume = min(h, 10.67)*34.2808
  if h > 10.67:
    raise ValueError(f"Height {h} is too large for Cos_96_FL")
  return volume

#: Cos_96_FL
def Cos_96_FL(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    with_lid=with_lid,
    model="Cos_96_FL",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=10.67,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_FL,
    ),
  )


def _compute_volume_from_height_Cos_96_Filter(h: float):
  volume = min(h, 12.2)*34.7486
  if h > 12.2:
    raise ValueError(f"Height {h} is too large for Cos_96_Filter")
  return volume

#: Cos_96_Filter
def Cos_96_Filter(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    with_lid=with_lid,
    model="Cos_96_Filter",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=2.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=12.2,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_Filter,
    ),
  )


#: Cos_96_Filter_L
def Cos_96_Filter_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Filter(name=name, with_lid=with_lid)


#: Cos_96_Filter_P
def Cos_96_Filter_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Filter(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_HalfArea(h: float):
  volume = min(h, 10.7)*17.7369
  if h > 10.7:
    raise ValueError(f"Height {h} is too large for Cos_96_HalfArea")
  return volume

#: Cos_96_HalfArea
def Cos_96_HalfArea(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    with_lid=with_lid,
    model="Cos_96_HalfArea",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=10.7,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_HalfArea,
    ),
  )


#: Cos_96_HalfArea_L
def Cos_96_HalfArea_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_HalfArea(name=name, with_lid=with_lid)


#: Cos_96_HalfArea_P
def Cos_96_HalfArea_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_HalfArea(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_PCR(h: float):
  volume = min(h, 11.5)*6.5450
  if h <= 20.5:
    volume += (h-11.5)*23.8237
  if h > 32.0:
    raise ValueError(f"Height {h} is too large for Cos_96_PCR")
  return volume

#: Cos_96_PCR
def Cos_96_PCR(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=22.5,
    with_lid=with_lid,
    model="Cos_96_PCR",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=0.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=20.5,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_PCR,
    ),
  )


#: Cos_96_PCR_L
def Cos_96_PCR_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_PCR(name=name, with_lid=with_lid)


#: Cos_96_PCR_P
def Cos_96_PCR_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_PCR(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_ProtCryst(h: float):
  volume = min(h, 1.6)*7.5477
  if h > 1.6:
    raise ValueError(f"Height {h} is too large for Cos_96_ProtCryst")
  return volume

#: Cos_96_ProtCryst
def Cos_96_ProtCryst(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=20.0,
    with_lid=with_lid,
    model="Cos_96_ProtCryst",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=8,
      dx=9.45,
      dy=7.0,
      dz=10.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=4.5,
      size_y=9.0,
      size_z=1.6,
      bottom_type=WellBottomType.FLAT,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_ProtCryst,
    ),
  )


#: Cos_96_ProtCryst_L
def Cos_96_ProtCryst_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_ProtCryst(name=name, with_lid=with_lid)


#: Cos_96_ProtCryst_P
def Cos_96_ProtCryst_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_ProtCryst(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_Rd(h: float):
  volume = min(h, 0.6)*min(h, 0.6)*(10.0531 - 1.0472*min(h, 0.6))
  if h <= 11.3:
    volume += (h-0.6)*34.7486
  if h > 11.9:
    raise ValueError(f"Height {h} is too large for Cos_96_Rd")
  return volume

#: Cos_96_Rd
def Cos_96_Rd(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    with_lid=with_lid,
    model="Cos_96_Rd",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=0.75,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=11.3,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_Rd,
    ),
  )


#: Cos_96_Rd_L
def Cos_96_Rd_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Rd(name=name, with_lid=with_lid)


#: Cos_96_Rd_P
def Cos_96_Rd_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Rd(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_SpecOps(h: float):
  volume = min(h, 11.0)*34.7486
  if h > 11.0:
    raise ValueError(f"Height {h} is too large for Cos_96_SpecOps")
  return volume

#: Cos_96_SpecOps
def Cos_96_SpecOps(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.3,
    with_lid=with_lid,
    model="Cos_96_SpecOps",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=0.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=11.0,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_SpecOps,
    ),
  )


#: Cos_96_SpecOps_L
def Cos_96_SpecOps_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_SpecOps(name=name, with_lid=with_lid)


#: Cos_96_SpecOps_P
def Cos_96_SpecOps_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_SpecOps(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_UV(h: float):
  volume = min(h, 11.0)*34.7486
  if h > 11.0:
    raise ValueError(f"Height {h} is too large for Cos_96_UV")
  return volume

#: Cos_96_UV
def Cos_96_UV(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.3,
    with_lid=with_lid,
    model="Cos_96_UV",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=11.0,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_UV,
    ),
  )


#: Cos_96_UV_L
def Cos_96_UV_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_UV(name=name, with_lid=with_lid)


#: Cos_96_UV_P
def Cos_96_UV_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_UV(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_Vb(h: float):
  volume = min(h, 1.4)*10.5564
  if h <= 10.9:
    volume += (h-1.4)*36.9605
  if h > 12.3:
    raise ValueError(f"Height {h} is too large for Cos_96_Vb")
  return volume

#: Cos_96_Vb
def Cos_96_Vb(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    with_lid=with_lid,
    model="Cos_96_Vb",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=10.9,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_Vb,
    ),
  )


#: Cos_96_Vb_L
def Cos_96_Vb_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Vb(name=name, with_lid=with_lid)


#: Cos_96_Vb_P
def Cos_96_Vb_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Vb(name=name, with_lid=with_lid).rotated(90)

