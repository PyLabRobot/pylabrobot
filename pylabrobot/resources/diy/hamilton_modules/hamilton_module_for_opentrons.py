
def MFX_opentrons_module(name: str) -> ResourceHolder:
  """3D printed part
  Module to position a opentrons module
  """

  # resource_size_x=127.76,
  # resource_size_y=85.48,

  return ResourceHolder(
    name=name,
    size_x=134.0,
    size_y=94.0,
    size_z=7,  # designed
    # probe height - carrier_height - deck_height
    child_location=Coordinate(4, 3, 4),
    model=MFX_opentrons_module.__name__,
  )
