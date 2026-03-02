""" Corning plates. """

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_container_1segment_round_fbottom,
  calculate_liquid_height_in_container_2segments_square_vbottom,
  calculate_liquid_volume_container_1segment_round_fbottom,
  calculate_liquid_volume_container_2segments_square_vbottom,
)
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# # # # # # # # # # Cor_96_wellplate_360ul_Fb # # # # # # # # # #


def Cor_96_wellplate_360ul_Fb(name: str, with_lid: bool = False) -> Plate:
  """
  Corning cat. no.s: 3603
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/
    Assay-Microplates/96-Well-Microplates/Corning%C2%AE-96-well-Black-Clear-and-White-Clear-
    Bottom-Polystyrene-Microplates/p/3603
  - brand: Corning
  - distributor: (Fisher Scientific, 10530753)
  - material: Polypropylene
  - tc_treated: Yes
  - tech_drawing: tech_drawings/Cor_96_wellplate_360ul_Fb.pdf
  - sterile: yes
  - notes:
      - Well bottom (i.e. material_z_thickness) is 60% thinner than conventional polystyrene
        microplates, resulting in lower background fluorescence & enabling readings down to 340 nm.
      - Opaque walls prevent well-to-well cross-talk.
  """

  # This used to be Cos_96_EZWash in the Esvelt lab

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.2,
    lid=Cor_96_wellplate_360ul_Fb_Lid(name=name + "_lid") if with_lid else None,
    model="Cor_96_wellplate_360ul_Fb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.87,  # 14.3-6.86/2
      dy=7.77,  # 11.2-6.86/2
      dz=3.03,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.86,  # top
      size_y=6.86,  # top
      size_z=10.67,
      material_z_thickness=0.5,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=360,
      compute_volume_from_height=_compute_volume_from_height_Cor_96_wellplate_360ul_Fb,
      compute_height_from_volume=_compute_height_from_volume_Cor_96_wellplate_360ul_Fb,
    ),
  )


def Cor_96_wellplate_360ul_Fb_Lid(name: str) -> Lid:
  """
  - brand: Corning
  """

  return Lid(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=8.9,  # measure the total z height
    nesting_z_height=7.6,  # measure overlap between lid and plate
    model="Cor_96_wellplate_360ul_Fb_Lid",
  )


# Volume-height functions
def _compute_volume_from_height_Cor_96_wellplate_360ul_Fb(h: float) -> float:
  return calculate_liquid_volume_container_1segment_round_fbottom(
    d=6.86, h_cylinder=10.67, liquid_height=h
  )


def _compute_height_from_volume_Cor_96_wellplate_360ul_Fb(liquid_volume: float) -> float:
  return calculate_liquid_height_container_1segment_round_fbottom(
    d=6.86, h_cylinder=10.67, liquid_volume=liquid_volume
  )


# Previous names in PLR:
def Cos_96_EZWash(name: str, with_lid: bool = False) -> Plate:
  raise ValueError("Deprecated. You probably want to use Cor_96_wellplate_360ul_Fb instead.")


# # # # # # # # # # Cor_96_wellplate_2mL_Vb # # # # # # # # # #


def Cor_96_wellplate_2mL_Vb(name: str, with_lid: bool = False) -> Plate:
  """
  Corning cat. no.: 3960
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Genomics-%26-
    Molecular-Biology/Automation-Consumables/Deep-Well-Plate/Corning%C2%AE-96-well-
    Polypropylene-Storage-Blocks/p/3960
  - brand: Corning
  - distributor: (Fisher Scientific, 10708212)
  - material: Polypropylene
  - sterile: yes
  - notes:
      - features uniform skirt heights for greater robotic gripping surface.
  """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=43.5,
    lid=Cor_96_wellplate_2mL_Vb_Lid(name=name + "_lid") if with_lid else None,
    model="Cor_Cos_96_wellplate_2mL_Vb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.6,
      dy=7.0,
      dz=1.2,
      item_dx=9.0,
      item_dy=9.0,
      size_x=8.0,
      size_y=8.0,
      size_z=42.0,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.25,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Cor_96_wellplate_2mL_Vb,
      compute_height_from_volume=_compute_height_from_volume_Cor_96_wellplate_2mL_Vb,
    ),
  )


def Cor_96_wellplate_2mL_Vb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")


# Volume-height functions
def _compute_volume_from_height_Cor_96_wellplate_2mL_Vb(
  h: float,
) -> float:
  if h > 44.1:  # 5% tolerance
    raise ValueError(f"Height {h} is too large for Cor_96_wellplate_2mL_Vb")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=7.8, y=7.8, h_pyramid=4.0, h_cube=38.0, liquid_height=h
  )


def _compute_height_from_volume_Cor_96_wellplate_2mL_Vb(
  liquid_volume: float,
):
  if liquid_volume > 2_100:  # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for Cor_96_wellplate_2mL_Vb")
  return round(
    calculate_liquid_height_in_container_2segments_square_vbottom(
      x=7.8,
      y=7.8,
      h_pyramid=4.0,
      h_cube=38.0,
      liquid_volume=liquid_volume,
    ),
    3,
  )


# Previous names in PLR:
def Cos_96_DWP_2mL_Vb(name: str, with_lid: bool = False) -> Plate:
  raise NotImplementedError(
    "This function is deprecated and will be removed in a future version."
    " Use 'Cor_96_wellplate_2mL_Vb' instead."
  )


def Cos_96_wellplate_2mL_Vb(name: str, with_lid: bool = False) -> Plate:
  raise NotImplementedError("deprecated. use Cor_96_wellplate_2mL_Vb instead")
