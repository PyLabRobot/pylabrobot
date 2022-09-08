""" Corning Costar plates """

# pylint: disable=invalid-name

from functools import partial

from pylabrobot.liquid_handling.resources.abstract import Plate


def _compute_volume_from_height_Cos_96_DW_1mL_P(h: float):
  return h*33.1831

#: Cos_96_DW_1mL_P
Cos_96_DW_1mL_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=42.0,
  dx=11.5,
  dy=14.0,
  dz=1.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=40.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_1mL_P,
)


def _compute_volume_from_height_Cos_384_Sq_Rd(h: float):
  return h*10.0800

#: Cos_384_Sq_Rd
Cos_384_Sq_Rd = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=11.6,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_Rd,
)


def _compute_volume_from_height_Cos_96_PCR_P(h: float):
  return h*23.8237

#: Cos_96_PCR_P
Cos_96_PCR_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=23.5,
  dx=11.5,
  dy=14.0,
  dz=0.5,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=20.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_PCR_P,
)


def _compute_volume_from_height_Cos_1536_10ul_P(h: float):
  return h*3.6100

#: Cos_1536_10ul_P
Cos_1536_10ul_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=10.25,
  dx=8.125,
  dy=10.625,
  dz=0.5,
  num_items_x=32,
  num_items_y=48,
  well_size_x=86.0,
  well_size_y=127.0,
  one_dot_max=5.75,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_1536_10ul_P,
)


def _compute_volume_from_height_Cos_96_ProtCryst_L(h: float):
  return h*7.5477

#: Cos_96_ProtCryst_L
Cos_96_ProtCryst_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=20.0,
  dx=11.7,
  dy=11.5,
  dz=10.0,
  num_items_x=24,
  num_items_y=8,
  well_size_x=127.0,
  well_size_y=9.0,
  one_dot_max=1.6,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_ProtCryst_L,
)


def _compute_volume_from_height_Cos_1536_10ul(h: float):
  return h*3.6100

#: Cos_1536_10ul
Cos_1536_10ul = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=10.25,
  dx=10.625,
  dy=8.125,
  dz=0.5,
  num_items_x=48,
  num_items_y=32,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=5.75,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_1536_10ul,
)


def _compute_volume_from_height_Cos_384_DW_P(h: float):
  return h*10.0800

#: Cos_384_DW_P
Cos_384_DW_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=28.0,
  dx=9.25,
  dy=11.75,
  dz=1.0,
  num_items_x=16,
  num_items_y=24,
  well_size_x=86.0,
  well_size_y=127.0,
  one_dot_max=24.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_DW_P,
)


def _compute_volume_from_height_Cos_96_Rd_P(h: float):
  return h*34.7486

#: Cos_96_Rd_P
Cos_96_Rd_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=20.0,
  dx=11.5,
  dy=14.0,
  dz=0.75,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.3,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Rd_P,
)


def _compute_volume_from_height_Cos_96_UV(h: float):
  return h*34.7486

#: Cos_96_UV
Cos_96_UV = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.3,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_UV,
)


def _compute_volume_from_height_Cos_96_Fl_L(h: float):
  return h*34.2808

#: Cos_96_Fl_L
Cos_96_Fl_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.24,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.67,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Fl_L,
)


def _compute_volume_from_height_Cos_96_EZWash_P(h: float):
  return h*37.3928

#: Cos_96_EZWash_P
Cos_96_EZWash_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.5,
  dx=11.5,
  dy=14.0,
  dz=1.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.3,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_EZWash_P,
)


def _compute_volume_from_height_Cos_96_DW_500ul(h: float):
  return h*34.7486

#: Cos_96_DW_500ul
Cos_96_DW_500ul = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=27.5,
  dx=14.0,
  dy=11.5,
  dz=2.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=25.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_500ul,
)


def _compute_volume_from_height_Cos_384_DW(h: float):
  return h*10.0800

#: Cos_384_DW
Cos_384_DW = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=28.0,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=24.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_DW,
)


def _compute_volume_from_height_Cos_96_SpecOps(h: float):
  return h*34.7486

#: Cos_96_SpecOps
Cos_96_SpecOps = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.3,
  dx=14.0,
  dy=11.5,
  dz=0.1,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_SpecOps,
)


def _compute_volume_from_height_Cos_384_Sq_Rd_L(h: float):
  return h*10.0800

#: Cos_384_Sq_Rd_L
Cos_384_Sq_Rd_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=11.6,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_Rd_L,
)


def _compute_volume_from_height_Cos_96_DW_2mL_P(h: float):
  return h*64.0000

#: Cos_96_DW_2mL_P
Cos_96_DW_2mL_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=43.5,
  dx=11.5,
  dy=14.0,
  dz=1.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=42.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_2mL_P,
)


def _compute_volume_from_height_Cos_96_Vb_P(h: float):
  return h*36.9605

#: Cos_96_Vb_P
Cos_96_Vb_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.24,
  dx=11.5,
  dy=14.0,
  dz=1.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.9,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Vb_P,
)


def _compute_volume_from_height_Cos_96_DW_500ul_P(h: float):
  return h*34.7486

#: Cos_96_DW_500ul_P
Cos_96_DW_500ul_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=27.5,
  dx=11.5,
  dy=14.0,
  dz=2.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=25.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_500ul_P,
)


def _compute_volume_from_height_Cos_384_PCR_P(h: float):
  return h*2.8510

#: Cos_384_PCR_P
Cos_384_PCR_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=16.0,
  dx=9.25,
  dy=11.75,
  dz=1.0,
  num_items_x=16,
  num_items_y=24,
  well_size_x=86.0,
  well_size_y=127.0,
  one_dot_max=9.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_PCR_P,
)


def _compute_volume_from_height_Cos_96_Rd(h: float):
  return h*34.7486

#: Cos_96_Rd
Cos_96_Rd = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=0.75,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.3,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Rd,
)


def _compute_volume_from_height_Cos_96_SpecOps_P(h: float):
  return h*34.7486

#: Cos_96_SpecOps_P
Cos_96_SpecOps_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.3,
  dx=11.5,
  dy=14.0,
  dz=0.1,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_SpecOps_P,
)


def _compute_volume_from_height_Cos_96_Filter_P(h: float):
  return h*34.7486

#: Cos_96_Filter_P
Cos_96_Filter_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.5,
  dx=11.5,
  dy=14.0,
  dz=2.1,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=12.2,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Filter_P,
)


def _compute_volume_from_height_Cos_96_ProtCryst(h: float):
  return h*7.5477

#: Cos_96_ProtCryst
Cos_96_ProtCryst = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=20.0,
  dx=11.7,
  dy=11.5,
  dz=10.0,
  num_items_x=24,
  num_items_y=8,
  well_size_x=127.0,
  well_size_y=9.0,
  one_dot_max=1.6,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_ProtCryst,
)


def _compute_volume_from_height_Cos_384_Sq_L(h: float):
  return h*12.2500

#: Cos_384_Sq_L
Cos_384_Sq_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.24,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=11.56,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_L,
)


def _compute_volume_from_height_Cos_96_UV_P(h: float):
  return h*34.7486

#: Cos_96_UV_P
Cos_96_UV_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.3,
  dx=11.5,
  dy=14.0,
  dz=1.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_UV_P,
)


def _compute_volume_from_height_Cos_96_HalfArea_P(h: float):
  return h*17.7369

#: Cos_96_HalfArea_P
Cos_96_HalfArea_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.5,
  dx=11.5,
  dy=14.0,
  dz=1.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.7,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_HalfArea_P,
)


def _compute_volume_from_height_Cos_96_DW_2mL_L(h: float):
  return h*64.0000

#: Cos_96_DW_2mL_L
Cos_96_DW_2mL_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=43.5,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=42.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_2mL_L,
)


def _compute_volume_from_height_Cos_384_Sq_Rd_P(h: float):
  return h*10.0800

#: Cos_384_Sq_Rd_P
Cos_384_Sq_Rd_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.5,
  dx=9.25,
  dy=11.75,
  dz=1.0,
  num_items_x=16,
  num_items_y=24,
  well_size_x=86.0,
  well_size_y=127.0,
  one_dot_max=11.6,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_Rd_P,
)


def _compute_volume_from_height_Cos_96_EZWash(h: float):
  return h*37.3928

#: Cos_96_EZWash
Cos_96_EZWash = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.3,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_EZWash,
)


def _compute_volume_from_height_Cos_96_FL(h: float):
  return h*34.2808

#: Cos_96_FL
Cos_96_FL = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.24,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.67,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_FL,
)


def _compute_volume_from_height_Cos_384_Sq(h: float):
  return h*12.2500

#: Cos_384_Sq
Cos_384_Sq = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.24,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=11.56,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq,
)


def _compute_volume_from_height_Cos_96_HalfArea_L(h: float):
  return h*17.7369

#: Cos_96_HalfArea_L
Cos_96_HalfArea_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.7,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_HalfArea_L,
)


def _compute_volume_from_height_Cos_96_UV_L(h: float):
  return h*34.7486

#: Cos_96_UV_L
Cos_96_UV_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.3,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_UV_L,
)


def _compute_volume_from_height_Cos_384_Sq_P(h: float):
  return h*12.2500

#: Cos_384_Sq_P
Cos_384_Sq_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.24,
  dx=9.25,
  dy=11.75,
  dz=1.0,
  num_items_x=16,
  num_items_y=24,
  well_size_x=86.0,
  well_size_y=127.0,
  one_dot_max=11.56,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_P,
)


def _compute_volume_from_height_Cos_96_DW_1mL(h: float):
  return h*33.1831

#: Cos_96_DW_1mL
Cos_96_DW_1mL = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=42.0,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=40.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_1mL,
)


def _compute_volume_from_height_Cos_96_HalfArea(h: float):
  return h*17.7369

#: Cos_96_HalfArea
Cos_96_HalfArea = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.7,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_HalfArea,
)


def _compute_volume_from_height_Cos_96_Filter_L(h: float):
  return h*34.7486

#: Cos_96_Filter_L
Cos_96_Filter_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=2.1,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=12.2,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Filter_L,
)


def _compute_volume_from_height_Cos_96_SpecOps_L(h: float):
  return h*34.7486

#: Cos_96_SpecOps_L
Cos_96_SpecOps_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.3,
  dx=14.0,
  dy=11.5,
  dz=0.1,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_SpecOps_L,
)


def _compute_volume_from_height_Cos_384_PCR_L(h: float):
  return h*2.8510

#: Cos_384_PCR_L
Cos_384_PCR_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=16.0,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=9.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_PCR_L,
)


def _compute_volume_from_height_Cos_96_DW_500ul_L(h: float):
  return h*34.7486

#: Cos_96_DW_500ul_L
Cos_96_DW_500ul_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=27.5,
  dx=14.0,
  dy=11.5,
  dz=2.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=25.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_500ul_L,
)


def _compute_volume_from_height_Cos_96_Vb_L(h: float):
  return h*36.9605

#: Cos_96_Vb_L
Cos_96_Vb_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.24,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.9,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Vb_L,
)


def _compute_volume_from_height_Cos_96_Filter(h: float):
  return h*34.7486

#: Cos_96_Filter
Cos_96_Filter = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=2.1,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=12.2,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Filter,
)


def _compute_volume_from_height_Cos_96_ProtCryst_P(h: float):
  return h*7.5477

#: Cos_96_ProtCryst_P
Cos_96_ProtCryst_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=20.0,
  dx=11.5,
  dy=11.7,
  dz=10.0,
  num_items_x=8,
  num_items_y=24,
  well_size_x=9.0,
  well_size_y=127.0,
  one_dot_max=1.6,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_ProtCryst_P,
)


def _compute_volume_from_height_Cos_1536_10ul_L(h: float):
  return h*3.6100

#: Cos_1536_10ul_L
Cos_1536_10ul_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=10.25,
  dx=10.625,
  dy=8.125,
  dz=0.5,
  num_items_x=48,
  num_items_y=32,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=5.75,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_1536_10ul_L,
)


def _compute_volume_from_height_Cos_96_Vb(h: float):
  return h*36.9605

#: Cos_96_Vb
Cos_96_Vb = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.24,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.9,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Vb,
)


def _compute_volume_from_height_Cos_96_PCR(h: float):
  return h*23.8237

#: Cos_96_PCR
Cos_96_PCR = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=22.5,
  dx=14.0,
  dy=11.5,
  dz=0.5,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=20.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_PCR,
)


def _compute_volume_from_height_Cos_96_PCR_L(h: float):
  return h*23.8237

#: Cos_96_PCR_L
Cos_96_PCR_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=23.5,
  dx=14.0,
  dy=11.5,
  dz=0.5,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=20.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_PCR_L,
)


def _compute_volume_from_height_Cos_96_DW_1mL_L(h: float):
  return h*33.1831

#: Cos_96_DW_1mL_L
Cos_96_DW_1mL_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=42.0,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=40.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_1mL_L,
)


def _compute_volume_from_height_Cos_96_EZWash_L(h: float):
  return h*37.3928

#: Cos_96_EZWash_L
Cos_96_EZWash_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.3,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_EZWash_L,
)


def _compute_volume_from_height_Cos_96_Fl_P(h: float):
  return h*34.2808

#: Cos_96_Fl_P
Cos_96_Fl_P = partial(Plate,
  size_x=86.0,
  size_y=127.0,
  size_z=14.24,
  dx=11.5,
  dy=14.0,
  dz=1.0,
  num_items_x=8,
  num_items_y=12,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=10.67,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Fl_P,
)


def _compute_volume_from_height_Cos_96_DW_2mL(h: float):
  return h*64.0000

#: Cos_96_DW_2mL
Cos_96_DW_2mL = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=43.5,
  dx=14.0,
  dy=11.5,
  dz=1.0,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=42.0,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_2mL,
)


def _compute_volume_from_height_Cos_96_Rd_L(h: float):
  return h*34.7486

#: Cos_96_Rd_L
Cos_96_Rd_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=14.5,
  dx=14.0,
  dy=11.5,
  dz=0.75,
  num_items_x=12,
  num_items_y=8,
  well_size_x=9.0,
  well_size_y=9.0,
  one_dot_max=11.3,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_96_Rd_L,
)


def _compute_volume_from_height_Cos_384_PCR(h: float):
  return h*2.8510

#: Cos_384_PCR
Cos_384_PCR = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=16.0,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=9.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_PCR,
)


def _compute_volume_from_height_Cos_384_DW_L(h: float):
  return h*10.0800

#: Cos_384_DW_L
Cos_384_DW_L = partial(Plate,
  size_x=127.0,
  size_y=86.0,
  size_z=28.0,
  dx=11.75,
  dy=9.25,
  dz=1.0,
  num_items_x=24,
  num_items_y=16,
  well_size_x=127.0,
  well_size_y=86.0,
  one_dot_max=24.5,
  lid_height=10,
  compute_volume_from_height=_compute_volume_from_height_Cos_384_DW_L,
)
