""" ML Star MultiFleX (MFX) carriers """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.carrier import (
  MFXCarrier,
  Coordinate,
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
