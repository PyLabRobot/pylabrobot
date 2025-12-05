from pylabrobot.resources.carrier import Coordinate, ResourceHolder


def hamilton_mfx_opentrons_module(name: str) -> ResourceHolder:
  """3D printed part
  Module to hold a opentrons module on an MFX carrier. Bolts into carrier
  see STL file for printing
  https://cad.onshape.com/documents/71f70c40910fd15876f75d76/w/81912f5001c1f8dcb28dfd3b/e/da8c964d83d158897c596d21
  """

  # resource_size_x=127.76,
  # resource_size_y=85.48,

  return ResourceHolder(
    name=name,
    size_x=134.0,
    size_y=94.0,
    size_z=7,  # designed
    # probe height - carrier_height - deck_height
    child_location=Coordinate(2, 5, 6),
    model=hamilton_mfx_opentrons_module.__name__,
  )
