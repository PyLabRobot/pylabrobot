from pylabrobot.resources.carrier import Coordinate, PlateHolder

def Hamilton_MFX_plateholder_DWP_metal_tapped_10mm_3dprint(name: str) -> PlateHolder:
  """A Hamilton module is screwed onto 3d printed adapters.
  Hamilton MFX DWP Module (cat.-no. 188042 / 188042-00). It contains metal clamps at the corners.
  https://www.hamiltoncompany.com/other-robotics/188042
  The 3D prints rest on the carrier base and are secured with 3mm screws onto which the module is screwed.
  3D prints located here: https://cad.onshape.com/documents/87b79aea22945656e1849b61/w/1d28384d184c23a6551facf8/e/3313021cc0b2fe3c5e005547
  STL files can be found in this folder.
  Instructions found in post here: https://labautomation.io/t/adapters-for-hamilton-carrier-188039/6561"""

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
