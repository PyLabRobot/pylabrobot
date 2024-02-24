""" ML Star MultiFleX (MFX) carriers """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.carrier import (
  MFXCarrier,
  Coordinate,
  create_carrier_sites,
  create_homogeneous_carrier_sites
)


def MFX_CAR_L5_base(name: str) -> MFXCarrier:
  """ Hamilton cat. no.: 188039 """
  return MFXCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=18.195,
    sites=create_homogeneous_carrier_sites([
        Coordinate(0.0, 4.5, 18.195),
        Coordinate(0.0, 100.5, 18.195),
        Coordinate(0.0, 196.5, 18.195),
        Coordinate(0.0, 292.5, 18.195),
        Coordinate(0.0, 388.5, 18.195),
      ],
      site_size_x=135.0,
      site_size_y=94.0,
    ),
    model="MFX_CAR_L5_base"
  )


def MFX_CAR_L3_2single_1triplet(name: str) -> MFXCarrier:
  """ MFX carrier with MFX_site 0 and 1 empty and 2,3,4 occupied 
  by the MettlerToledoWXS205SDU scale in "back" orientation, i.e. 
  SBS-adapter is located towards the back of the machine.
  """

  return MFXCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=18.195,
    sites=create_carrier_sites([
        Coordinate(0.0, 4.5, 18.195),
        Coordinate(0.0, 100.5, 18.195),
        Coordinate(0.0, 196.5, 18.195),
      ],
      site_size_x=[135.0, 135.0, 135.0],
      site_size_y=[94., 94.0, (94.0)*3+4],
    ),
    model="MFX_CAR_L_0_1_2-4"
  )
