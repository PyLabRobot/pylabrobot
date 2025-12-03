from pylabrobot.resources.carrier import Coordinate, PlateHolder


class OTModule:
  """Any ot module like temperature controller, thermocycler, etc."""


def Opentrons_deep_well_aluminum_block(name: str) -> PlateHolder:
  """Aluminum Block – 96 Deep Well Plate (cat. 991-00211)
  https://opentrons.com/products/aluminum-block-96-deep-well-plate
  The Opentrons Deep Well Aluminum Block can be placed directly on 
  the OT-2/Opentrons Flex deck or on the Opentrons Temperature Module"""

  return PlateHolder(
    name=name,
    size_x=127.7,  # measured
    size_y=85.5,  # measured
    size_z=21.5,  # measured
    child_location=Coordinate(0, 0, 5.1),  # measured
    #pedestal_size_z=,
    model=Opentrons_deep_well_aluminum_block.__name__,
  )
