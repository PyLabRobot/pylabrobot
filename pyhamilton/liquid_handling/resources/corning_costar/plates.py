""" Corning Costar plates """

# pylint: skip-file

from pyhamilton.liquid_handling.resources.abstract import Plate


class Cos_96_DW_1mL_P(Plate):
  """ Cos_96_DW_1mL_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=42.0,
      dx=11.5,
      dy=14.0,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*33.1831


class Cos_384_Sq_Rd(Plate):
  """ Cos_384_Sq_Rd """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*10.0800


class Cos_96_PCR_P(Plate):
  """ Cos_96_PCR_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=23.5,
      dx=11.5,
      dy=14.0,
      dz=0.5
    )

  def compute_volume_from_height(self, h):
    return h*23.8237


class Cos_1536_10ul_P(Plate):
  """ Cos_1536_10ul_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=10.25,
      dx=8.125,
      dy=10.625,
      dz=0.5
    )

  def compute_volume_from_height(self, h):
    return h*3.6100


class Cos_96_ProtCryst_L(Plate):
  """ Cos_96_ProtCryst_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=20.0,
      dx=11.7,
      dy=11.5,
      dz=10.0
    )

  def compute_volume_from_height(self, h):
    return h*7.5477


class Cos_1536_10ul(Plate):
  """ Cos_1536_10ul """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=10.25,
      dx=10.625,
      dy=8.125,
      dz=0.5
    )

  def compute_volume_from_height(self, h):
    return h*3.6100


class Cos_384_DW_P(Plate):
  """ Cos_384_DW_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=28.0,
      dx=9.25,
      dy=11.75,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*10.0800


class Cos_96_Rd_P(Plate):
  """ Cos_96_Rd_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=20.0,
      dx=11.5,
      dy=14.0,
      dz=0.75
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_UV(Plate):
  """ Cos_96_UV """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.3,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_Fl_L(Plate):
  """ Cos_96_Fl_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.24,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*34.2808


class Cos_96_EZWash_P(Plate):
  """ Cos_96_EZWash_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.5,
      dx=11.5,
      dy=14.0,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*37.3928


class Cos_96_DW_500ul(Plate):
  """ Cos_96_DW_500ul """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=27.5,
      dx=14.0,
      dy=11.5,
      dz=2.0
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_384_DW(Plate):
  """ Cos_384_DW """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=28.0,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*10.0800


class Cos_96_SpecOps(Plate):
  """ Cos_96_SpecOps """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.3,
      dx=14.0,
      dy=11.5,
      dz=0.1
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_384_Sq_Rd_L(Plate):
  """ Cos_384_Sq_Rd_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*10.0800


class Cos_96_DW_2mL_P(Plate):
  """ Cos_96_DW_2mL_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=43.5,
      dx=11.5,
      dy=14.0,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*64.0000


class Cos_96_Vb_P(Plate):
  """ Cos_96_Vb_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.24,
      dx=11.5,
      dy=14.0,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*36.9605


class Cos_96_DW_500ul_P(Plate):
  """ Cos_96_DW_500ul_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=27.5,
      dx=11.5,
      dy=14.0,
      dz=2.0
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_384_PCR_P(Plate):
  """ Cos_384_PCR_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=16.0,
      dx=9.25,
      dy=11.75,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*2.8510


class Cos_96_Rd(Plate):
  """ Cos_96_Rd """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=0.75
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_SpecOps_P(Plate):
  """ Cos_96_SpecOps_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.3,
      dx=11.5,
      dy=14.0,
      dz=0.1
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_Filter_P(Plate):
  """ Cos_96_Filter_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.5,
      dx=11.5,
      dy=14.0,
      dz=2.1
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_ProtCryst(Plate):
  """ Cos_96_ProtCryst """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=20.0,
      dx=11.7,
      dy=11.5,
      dz=10.0
    )

  def compute_volume_from_height(self, h):
    return h*7.5477


class Cos_384_Sq_L(Plate):
  """ Cos_384_Sq_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.24,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*12.2500


class Cos_96_UV_P(Plate):
  """ Cos_96_UV_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.3,
      dx=11.5,
      dy=14.0,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_HalfArea_P(Plate):
  """ Cos_96_HalfArea_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.5,
      dx=11.5,
      dy=14.0,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*17.7369


class Cos_96_DW_2mL_L(Plate):
  """ Cos_96_DW_2mL_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=43.5,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*64.0000


class Cos_384_Sq_Rd_P(Plate):
  """ Cos_384_Sq_Rd_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.5,
      dx=9.25,
      dy=11.75,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*10.0800


class Cos_96_EZWash(Plate):
  """ Cos_96_EZWash """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*37.3928


class Cos_96_FL(Plate):
  """ Cos_96_FL """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.24,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*34.2808


class Cos_384_Sq(Plate):
  """ Cos_384_Sq """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.24,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*12.2500


class Cos_96_HalfArea_L(Plate):
  """ Cos_96_HalfArea_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*17.7369


class Cos_96_UV_L(Plate):
  """ Cos_96_UV_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.3,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_384_Sq_P(Plate):
  """ Cos_384_Sq_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.24,
      dx=9.25,
      dy=11.75,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*12.2500


class Cos_96_DW_1mL(Plate):
  """ Cos_96_DW_1mL """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=42.0,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*33.1831


class Cos_96_HalfArea(Plate):
  """ Cos_96_HalfArea """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*17.7369


class Cos_96_Filter_L(Plate):
  """ Cos_96_Filter_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=2.1
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_SpecOps_L(Plate):
  """ Cos_96_SpecOps_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.3,
      dx=14.0,
      dy=11.5,
      dz=0.1
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_384_PCR_L(Plate):
  """ Cos_384_PCR_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=16.0,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*2.8510


class Cos_96_DW_500ul_L(Plate):
  """ Cos_96_DW_500ul_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=27.5,
      dx=14.0,
      dy=11.5,
      dz=2.0
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_Vb_L(Plate):
  """ Cos_96_Vb_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.24,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*36.9605


class Cos_96_Filter(Plate):
  """ Cos_96_Filter """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=2.1
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_96_ProtCryst_P(Plate):
  """ Cos_96_ProtCryst_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=20.0,
      dx=11.5,
      dy=11.7,
      dz=10.0
    )

  def compute_volume_from_height(self, h):
    return h*7.5477


class Cos_1536_10ul_L(Plate):
  """ Cos_1536_10ul_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=10.25,
      dx=10.625,
      dy=8.125,
      dz=0.5
    )

  def compute_volume_from_height(self, h):
    return h*3.6100


class Cos_96_Vb(Plate):
  """ Cos_96_Vb """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.24,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*36.9605


class Cos_96_PCR(Plate):
  """ Cos_96_PCR """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=22.5,
      dx=14.0,
      dy=11.5,
      dz=0.5
    )

  def compute_volume_from_height(self, h):
    return h*23.8237


class Cos_96_PCR_L(Plate):
  """ Cos_96_PCR_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=23.5,
      dx=14.0,
      dy=11.5,
      dz=0.5
    )

  def compute_volume_from_height(self, h):
    return h*23.8237


class Cos_96_DW_1mL_L(Plate):
  """ Cos_96_DW_1mL_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=42.0,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*33.1831


class Cos_96_EZWash_L(Plate):
  """ Cos_96_EZWash_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*37.3928


class Cos_96_Fl_P(Plate):
  """ Cos_96_Fl_P """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=86.0,
      size_y=127.0,
      size_z=14.24,
      dx=11.5,
      dy=14.0,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*34.2808


class Cos_96_DW_2mL(Plate):
  """ Cos_96_DW_2mL """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=43.5,
      dx=14.0,
      dy=11.5,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*64.0000


class Cos_96_Rd_L(Plate):
  """ Cos_96_Rd_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=14.5,
      dx=14.0,
      dy=11.5,
      dz=0.75
    )

  def compute_volume_from_height(self, h):
    return h*34.7486


class Cos_384_PCR(Plate):
  """ Cos_384_PCR """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=16.0,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*2.8510


class Cos_384_DW_L(Plate):
  """ Cos_384_DW_L """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=28.0,
      dx=11.75,
      dy=9.25,
      dz=1.0
    )

  def compute_volume_from_height(self, h):
    return h*10.0800
