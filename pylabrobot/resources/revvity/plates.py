""" Revvity plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.itemized_resource import create_equally_spaced

from pylabrobot.resources.volume_functions import calculate_liquid_volume_container_2segments_round_vbottom


def _compute_volume_from_height_Revvity_ProxiPlate_384Plus(h: float):
  """ Simplification: instead of 3 segment (hemisphere-frustum of cone-cylinder) 
  -> 2 segment (cone-cylinder)
  """
  if h > 77:
    raise ValueError(f"Height {h} is too large for Revvity_ProxiPlate_384Plus")
  return calculate_liquid_volume_container_2segments_round_vbottom(
    d=3.3,
    h_cone = 3,
    h_cylinder = 2.3,
    liquid_height=h
  )


#: Revvity_ProxiPlate_384Plus
def Revvity_ProxiPlate_384Plus(name: str, with_lid: bool = False) -> Plate:
  # https://www.perkinelmer.com/uk/Product/proxiplate-384-plus-50w-6008280
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.35,
    with_lid=with_lid,
    model="Revvity_ProxiPlate_384Plus",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=10.45,
      dy=7.9,
      dz=3.3,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.3,
      size_y=3.3,
      size_z=5.3,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Revvity_ProxiPlate_384Plus,
    ),
  )
