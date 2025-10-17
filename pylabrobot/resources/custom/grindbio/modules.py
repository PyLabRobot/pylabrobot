from pylabrobot.resources.carrier import Coordinate, PlateHolder
from pylabrobot.resources.resource_holder import ResourceHolder


def Hamilton_MFX_plateholder_DWP_metal_tapped_10mm_3dprint(name: str) -> PlateHolder:
  """Hamilton MFX DWP Module (cat.-no. 188042 / 188042-00).
  It also contains metal clamps at the corners.
  https://www.hamiltoncompany.com/other-robotics/188042
  """

  return PlateHolder(
    name=name,
    size_x=135.0,  # measured
    size_y=94.0,  # measured
    size_z=29.8,  # measured
    # probe height - carrier_height - deck_height
    child_location=Coordinate(4.0, 4.0, 183.95 - 63.95 - 100),  # 20.0 measured
    pedestal_size_z=-4.74,
    model=Hamilton_MFX_plateholder_DWP_metal_tapped_10mm_3dprint.__name__,
  )
