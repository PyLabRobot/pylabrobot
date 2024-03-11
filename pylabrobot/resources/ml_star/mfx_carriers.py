""" ML Star MultiFleX (MFX) carriers """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.carrier import (
  MFXCarrier,
  ShakerCarrier,
  Coordinate,
  create_homogeneous_carrier_sites
)


def MFX_CAR_L5_base(name: str) -> MFXCarrier:
  """ Hamilton cat. no.: 188039
  Labware carrier base for up to 5 Multiflex Modules
  """
  return MFXCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=18.195,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.0, 8.5, 86.15),
        Coordinate(4.0, 104.5, 86.15),
        Coordinate(4.0, 200.5, 86.15),
        Coordinate(4.0, 296.5, 86.15),
        Coordinate(4.0, 392.5, 86.15)
        # Coordinate(0.0, 4.5, 18.195),
        # Coordinate(0.0, 100.5, 18.195),
        # Coordinate(0.0, 196.5, 18.195),
        # Coordinate(0.0, 292.5, 18.195),
        # Coordinate(0.0, 388.5, 18.195),
      ],
      site_size_x=135.0,
      site_size_y=94.0,
    ),
    model="MFX_CAR_L5_base"
  )


def PLT_CAR_L4_SHAKER(name: str) -> ShakerCarrier:
  """ Hamilton cat. no.: 187001
  Template carrier with 4 positions for Hamilton Heater Shaker
  (optional: Shaker H+P, Shaker Heater CAT) and plate bases (7T)
  """
  return ShakerCarrier(
    name=name,
    size_x=157.5,
    size_y=497.0,
    size_z=18.195,
    sites=create_homogeneous_carrier_sites([
        Coordinate(0.0, 4.5, 18.195),
        Coordinate(0.0, 100.5, 18.195),
        Coordinate(0.0, 196.5, 18.195),
        Coordinate(0.0, 292.5, 18.195),
      ],
      site_size_x=135.0,
      site_size_y=94.0,
    ),
    model="PLT_CAR_L4_SHAKER"
  )
