from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_volume_container_2segments_square_vbottom,
)
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# # # # # # # # # # Cor_Axy_24_wellplate_10mL_Vb # # # # # # # # # #


def Cor_Axy_24_wellplate_10mL_Vb(name: str, with_lid: bool = False) -> Plate:
  """
  Corning cat. no.: P-DW-10ML-24-C-S
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Genomics-&-Molecular-Biology/Automation-Consumables/Deep-Well-Plate/Axygen%C2%AE-Deep-Well-and-Assay-Plates/p/P-DW-10ML-24-C
  - brand: Axygen
  - distributor: (Fisher Scientific, 12557837)
  - material: Polypropylene
  - sterile: yes
  - autoclavable: yes
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=44.24,
    lid=Cor_Axy_24_wellplate_10mL_Vb_Lid(name + "_lid") if with_lid else None,
    model=Cor_Axy_24_wellplate_10mL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=6,
      num_items_y=4,
      dx=9.8,
      dy=7.2,
      dz=1.2,
      item_dx=18,
      item_dy=18,
      size_x=17.0,
      size_y=17.0,
      size_z=42,
      material_z_thickness=1.46,
      bottom_type=WellBottomType.V,
      compute_volume_from_height=_compute_volume_from_height_Cor_Axy_24_wellplate_10mL_Vb,
      cross_section_type=CrossSectionType.RECTANGLE,
    ),
  )


def Cor_Axy_24_wellplate_10mL_Vb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")


def _compute_volume_from_height_Cor_Axy_24_wellplate_10mL_Vb(h: float):
  if h > 42.1:
    raise ValueError(f"Height {h} is too large for Cos_96_Vb")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=17, y=17, h_pyramid=5, h_cube=37, liquid_height=h
  )
