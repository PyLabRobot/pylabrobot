""" Revvity plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.itemized_resource import create_equally_spaced


# TODO: update heights for volume calculations
def _compute_volume_from_height_Revvity_ProxiPlate_384Plus(h: float):
  # raise NotImplementedError("This function is not yet implemented")
  volume = min(h, 11.56)*12.2500
  if h > 11.56:
    raise ValueError(f"Height {h} is too large for Revvity_384Plus_ProxiPlate")
  return volume


#: Revvity_ProxiPlate_384Plus
def Revvity_ProxiPlate_384Plus(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    with_lid=with_lid,
    model="Revvity_ProxiPlate_384Plus",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.5,
      dy=7.0,
      dz=4.5,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=6.24,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Revvity_ProxiPlate_384Plus,
    ),
  )

