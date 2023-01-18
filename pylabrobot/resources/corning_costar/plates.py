""" Corning Costar plates """

# pylint: disable=invalid-name

from pylabrobot.resources import Plate, Well, create_equally_spaced


def _compute_volume_from_height_Cos_1536_10ul(h: float):
  return h*3.6100

#: Cos_1536_10ul
def Cos_1536_10ul(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=10.25,
    one_dot_max=5.75,
    with_lid=with_lid,
    model="Cos_1536_10ul",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_1536_10ul,
    items=create_equally_spaced(Well,
      num_items_x=48,
      num_items_y=32,
      dx=-52.875,
      dy=-34.875,
      dz=0.5,
      item_size_x=127.0,
      item_size_y=86.0,
    ),
  )


#: Cos_1536_10ul_L
def Cos_1536_10ul_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_1536_10ul(name=name, with_lid=with_lid)


#: Cos_1536_10ul_P
def Cos_1536_10ul_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_1536_10ul(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_DW(h: float):
  return h*10.0800

#: Cos_384_DW
def Cos_384_DW(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=28.0,
    one_dot_max=24.5,
    with_lid=with_lid,
    model="Cos_384_DW",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_384_DW,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=-51.75,
      dy=-33.75,
      dz=1.0,
      item_size_x=127.0,
      item_size_y=86.0,
    ),
  )


#: Cos_384_DW_L
def Cos_384_DW_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_DW(name=name, with_lid=with_lid)


#: Cos_384_DW_P
def Cos_384_DW_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_DW(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_PCR(h: float):
  return h*2.8510

#: Cos_384_PCR
def Cos_384_PCR(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=16.0,
    one_dot_max=9.5,
    with_lid=with_lid,
    model="Cos_384_PCR",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_384_PCR,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=-51.75,
      dy=-33.75,
      dz=1.0,
      item_size_x=127.0,
      item_size_y=86.0,
    ),
  )


#: Cos_384_PCR_L
def Cos_384_PCR_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_PCR(name=name, with_lid=with_lid)


#: Cos_384_PCR_P
def Cos_384_PCR_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_PCR(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_Sq(h: float):
  return h*12.2500

#: Cos_384_Sq
def Cos_384_Sq(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    one_dot_max=11.56,
    with_lid=with_lid,
    model="Cos_384_Sq",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=-51.75,
      dy=-33.75,
      dz=1.0,
      item_size_x=127.0,
      item_size_y=86.0,
    ),
  )


#: Cos_384_Sq_L
def Cos_384_Sq_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq(name=name, with_lid=with_lid)


#: Cos_384_Sq_P
def Cos_384_Sq_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_384_Sq_Rd(h: float):
  return h*10.0800

#: Cos_384_Sq_Rd
def Cos_384_Sq_Rd(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    one_dot_max=11.6,
    with_lid=with_lid,
    model="Cos_384_Sq_Rd",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_Rd,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=-51.75,
      dy=-33.75,
      dz=1.0,
      item_size_x=127.0,
      item_size_y=86.0,
    ),
  )


#: Cos_384_Sq_Rd_L
def Cos_384_Sq_Rd_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq_Rd(name=name, with_lid=with_lid)


#: Cos_384_Sq_Rd_P
def Cos_384_Sq_Rd_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_384_Sq_Rd(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_DW_1mL(h: float):
  return h*33.1831

#: Cos_96_DW_1mL
def Cos_96_DW_1mL(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=42.0,
    one_dot_max=40.0,
    with_lid=with_lid,
    model="Cos_96_DW_1mL",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_1mL,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_DW_1mL_L
def Cos_96_DW_1mL_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_1mL(name=name, with_lid=with_lid)


#: Cos_96_DW_1mL_P
def Cos_96_DW_1mL_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_1mL(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_DW_2mL(h: float):
  return h*64.0000

#: Cos_96_DW_2mL
def Cos_96_DW_2mL(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=43.5,
    one_dot_max=42.0,
    with_lid=with_lid,
    model="Cos_96_DW_2mL",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_2mL,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_DW_2mL_L
def Cos_96_DW_2mL_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_2mL(name=name, with_lid=with_lid)


#: Cos_96_DW_2mL_P
def Cos_96_DW_2mL_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_2mL(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_DW_500ul(h: float):
  return h*34.7486

#: Cos_96_DW_500ul
def Cos_96_DW_500ul(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=27.5,
    one_dot_max=25.0,
    with_lid=with_lid,
    model="Cos_96_DW_500ul",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_500ul,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=2.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_DW_500ul_L
def Cos_96_DW_500ul_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_500ul(name=name, with_lid=with_lid)


#: Cos_96_DW_500ul_P
def Cos_96_DW_500ul_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_DW_500ul(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_EZWash(h: float):
  return h*37.3928

#: Cos_96_EZWash
def Cos_96_EZWash(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    one_dot_max=11.3,
    with_lid=with_lid,
    model="Cos_96_EZWash",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_EZWash,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_EZWash_L
def Cos_96_EZWash_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_EZWash(name=name, with_lid=with_lid)


#: Cos_96_EZWash_P
def Cos_96_EZWash_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_EZWash(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_FL(h: float):
  return h*34.2808

#: Cos_96_FL
def Cos_96_FL(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    one_dot_max=10.67,
    with_lid=with_lid,
    model="Cos_96_FL",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_FL,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


def _compute_volume_from_height_Cos_96_Filter(h: float):
  return h*34.7486

#: Cos_96_Filter
def Cos_96_Filter(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    one_dot_max=12.2,
    with_lid=with_lid,
    model="Cos_96_Filter",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_Filter,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=2.1,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_Filter_L
def Cos_96_Filter_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Filter(name=name, with_lid=with_lid)


#: Cos_96_Filter_P
def Cos_96_Filter_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Filter(name=name, with_lid=with_lid).rotated(90)


#: Cos_96_Fl_L
def Cos_96_Fl_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_FL(name=name, with_lid=with_lid)


#: Cos_96_Fl_P
def Cos_96_Fl_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_FL(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_HalfArea(h: float):
  return h*17.7369

#: Cos_96_HalfArea
def Cos_96_HalfArea(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    one_dot_max=10.7,
    with_lid=with_lid,
    model="Cos_96_HalfArea",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_HalfArea,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_HalfArea_L
def Cos_96_HalfArea_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_HalfArea(name=name, with_lid=with_lid)


#: Cos_96_HalfArea_P
def Cos_96_HalfArea_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_HalfArea(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_PCR(h: float):
  return h*23.8237

#: Cos_96_PCR
def Cos_96_PCR(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=22.5,
    one_dot_max=20.5,
    with_lid=with_lid,
    model="Cos_96_PCR",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_PCR,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=0.5,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_PCR_L
def Cos_96_PCR_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_PCR(name=name, with_lid=with_lid)


#: Cos_96_PCR_P
def Cos_96_PCR_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_PCR(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_ProtCryst(h: float):
  return h*7.5477

#: Cos_96_ProtCryst
def Cos_96_ProtCryst(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=20.0,
    one_dot_max=1.6,
    with_lid=with_lid,
    model="Cos_96_ProtCryst",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_ProtCryst,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=8,
      dx=-51.8,
      dy=7.0,
      dz=10.0,
      item_size_x=127.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_ProtCryst_L
def Cos_96_ProtCryst_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_ProtCryst(name=name, with_lid=with_lid)


#: Cos_96_ProtCryst_P
def Cos_96_ProtCryst_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_ProtCryst(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_Rd(h: float):
  return h*34.7486

#: Cos_96_Rd
def Cos_96_Rd(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    one_dot_max=11.3,
    with_lid=with_lid,
    model="Cos_96_Rd",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_Rd,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=0.75,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_Rd_L
def Cos_96_Rd_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Rd(name=name, with_lid=with_lid)


#: Cos_96_Rd_P
def Cos_96_Rd_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Rd(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_SpecOps(h: float):
  return h*34.7486

#: Cos_96_SpecOps
def Cos_96_SpecOps(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.3,
    one_dot_max=11.0,
    with_lid=with_lid,
    model="Cos_96_SpecOps",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_SpecOps,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=0.1,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_SpecOps_L
def Cos_96_SpecOps_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_SpecOps(name=name, with_lid=with_lid)


#: Cos_96_SpecOps_P
def Cos_96_SpecOps_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_SpecOps(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_UV(h: float):
  return h*34.7486

#: Cos_96_UV
def Cos_96_UV(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.3,
    one_dot_max=11.0,
    with_lid=with_lid,
    model="Cos_96_UV",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_UV,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_UV_L
def Cos_96_UV_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_UV(name=name, with_lid=with_lid)


#: Cos_96_UV_P
def Cos_96_UV_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_UV(name=name, with_lid=with_lid).rotated(90)


def _compute_volume_from_height_Cos_96_Vb(h: float):
  return h*36.9605

#: Cos_96_Vb
def Cos_96_Vb(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    one_dot_max=10.9,
    with_lid=with_lid,
    model="Cos_96_Vb",
    lid_height=10,
    compute_volume_from_height=_compute_volume_from_height_Cos_96_Vb,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      dy=7.0,
      dz=1.0,
      item_size_x=9.0,
      item_size_y=9.0,
    ),
  )


#: Cos_96_Vb_L
def Cos_96_Vb_L(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Vb(name=name, with_lid=with_lid)


#: Cos_96_Vb_P
def Cos_96_Vb_P(name: str, with_lid: bool = False) -> Plate:
  return Cos_96_Vb(name=name, with_lid=with_lid).rotated(90)
