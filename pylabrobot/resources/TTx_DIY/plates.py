""" T-Therapeutics (TTx) plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.container import Container
from pylabrobot.resources.itemized_resource import create_equally_spaced

from pylabrobot.resources.volume_functions import *


def _compute_volume_from_height_TTx_24_EppiPlate(h: float):
  if h > 77:
    raise ValueError(f"Height {h} is too large for TTx_24_EppiPlate")
  return calculate_liquid_volume_container_2segments_round_Vb( # Simplification: instead of 3 segment (hemisphere-frustum of cone-cylinder) -> 2 segment (cone-cylinder)
    d=9,
    h_cone = 18, 
    h_cylinder = 20, 
    liquid_height=36
    )


#: TTx_24_EppiPlate (DIY (3D-printed) tube rack holding 24x 1.5mL Eppendorf tubes)
def TTx_24_EppiPlate(name: str, with_lid: bool = False) -> Plate:
  # TODO: Add link to STL file once website for download is set up
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=41.9,
    with_lid=with_lid,
    model="TTx_24_EppiPlate",
    lid_height=0,
    items=create_equally_spaced(Well,
        num_items_x=6,
        num_items_y=4,
        dx=14.25,
        dy=12.5,
        dz=9.0,
        item_dx=19.5,
        item_dy=19.5,
        size_x=9,
        size_y=9,
        size_z=38,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_TTx_24_EppiPlate,
    ),
  )

