""" Revvity plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_ordered_items_2d

from pylabrobot.resources.height_volume_functions import calculate_liquid_volume_container_2segments_round_vbottom


# # # # # # # # # # Revvity_384_wellplate_28ul_Ub_Lid # # # # # # # # # #

def _compute_volume_from_height_Revvity_384_wellplate_28ul_Ub(h: float):
  """ Simplification: instead of 3 segment (hemisphere-frustum of cone-cylinder)
  -> 2 segment (cone-cylinder)
  """
  if h > 77:
    raise ValueError(f"Height {h} is too large for Revvity_384_wellplate_28ul_Ub")
  return calculate_liquid_volume_container_2segments_round_vbottom(
    d=3.3,
    h_cone = 3,
    h_cylinder = 2.3,
    liquid_height=h
  )


def Revvity_384_wellplate_28ul_Ub_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Revvity_384_wellplate_28ul_Ub_Lid",
  # )


#: Revvity_384_wellplate_28ul_Ub
def Revvity_384_wellplate_28ul_Ub(name: str, with_lid: bool = False) -> Plate:
  """ Revvity cat. no.: 6008280. nickname "ProxiPlate-384 Plus"
  - Material: Polystyrene
  - Colour: white
  - "shallow-well"
  - Sterilization compatibility: ?
  - Chemical resistance:?
  - Thermal resistance: ?
  - Surface treatment: non-treated
  - Sealing options: ?
  - Cleanliness: non-sterile
  - Automation compatibility: not specifically declared
  - Total volume = 28 ul
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.35,
    lid=Revvity_384_wellplate_28ul_Ub_Lid(name + "_lid") if with_lid else None,
    model="Revvity_384_wellplate_28ul_Ub",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      dx=8.83,
      dy=5.69,
      dz=8.2,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.3,
      size_y=3.3,
      size_z=5.3,
      bottom_type=WellBottomType.U,
      material_z_thickness=1.0,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Revvity_384_wellplate_28ul_Ub,
    ),
  )
