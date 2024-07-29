""" Porvair plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_ordered_items_2d

from pylabrobot.resources.height_volume_functions import calculate_liquid_volume_container_2segments_round_vbottom


def _compute_volume_from_height_Azenta4titudeFrameStar_96_wellplate_200ul_Vb(h: float):
  if h > 15.1:
    raise ValueError(f"Height {h} is too large for Azenta4titudeFrameStar_96_wellplate_200ul_Vb")
  return calculate_liquid_volume_container_2segments_round_vbottom(
    d=5.5,
    h_cone=9.8,
    h_cylinder=5.3,
    liquid_height=h)

def Azenta4titudeFrameStar_96_wellplate_200ul_Vb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=5,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Azenta4titudeFrameStar_96_wellplate_200ul_Vb_Lid",
  # )


def Azenta4titudeFrameStar_96_wellplate_skirted(name: str, with_lid: bool = False) -> Plate:
  raise NotImplementedError("This function is deprecated and will be removed in a future version."
          " Use 'Azenta4titudeFrameStar_96_wellplate_200ul_Vb' instead.")


#: Azenta4titudeFrameStar_96_wellplate_skirted
def Azenta4titudeFrameStar_96_wellplate_200ul_Vb(name: str, with_lid: bool = False) -> Plate:
  """ Azenta cat. no.: 4ti-0960.
  - Material: Polypropylene wells, polycarbonate frame
  - Sterilization compatibility: ?
  - Chemical resistance: ?
  - Thermal resistance: ?
  - Sealing options: ?
  - Cleanliness: ?
  - Automation compatibility: "Rigid frame eliminates warping and distortion during
    PCR. Ideal for use with robotic systems.' -> extra  rigid skirt option (4ti-0960/RIG)
    available.
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=16.1,
    lid=Azenta4titudeFrameStar_96_wellplate_200ul_Vb_Lid(name + "_lid") if with_lid else None,
    model="Azenta4titudeFrameStar_96_wellplate_200ul_Vb",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.0,
      dy=8.49,
      dz=0.8,
      item_dx=9,
      item_dy=9,
      size_x=5.5,
      size_y=5.5,
      size_z=15.1,
      bottom_type=WellBottomType.V,
      material_z_thickness=0.73,
      compute_volume_from_height=(
        _compute_volume_from_height_Azenta4titudeFrameStar_96_wellplate_200ul_Vb
        ),
      cross_section_type=CrossSectionType.CIRCLE
    ),
  )


#: Azenta4titudeFrameStar_96_wellplate_Vb_L
def Azenta4titudeFrameStar_96_wellplate_200ul_Vb_L(name: str, with_lid: bool = False) -> Plate:
  return Azenta4titudeFrameStar_96_wellplate_200ul_Vb(name=name, with_lid=with_lid)


#: Azenta4titudeFrameStar_96_wellplate_Vb_P
def Azenta4titudeFrameStar_96_wellplate_200ul_Vb_P(name: str, with_lid: bool = False) -> Plate:
  return Azenta4titudeFrameStar_96_wellplate_200ul_Vb(name=name, with_lid=with_lid).rotated(z=90)
