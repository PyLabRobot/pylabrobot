""" ML Star MultiFleX (MFX) carriers """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.carrier import (
  Coordinate,
  CarrierSite,
  MFXCarrier,
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
    sites=create_homogeneous_carrier_sites(klass=CarrierSite, locations=[
        Coordinate(0.0, 5.0, 18.195),
        Coordinate(0.0, 101.0, 18.195),
        Coordinate(0.0, 197.0, 18.195),
        Coordinate(0.0, 293.0, 18.195),
        Coordinate(0.0, 389.0, 18.195)
      ],
      site_size_x=135.0,
      site_size_y=94.0,
    ),
    model="MFX_CAR_L5_base"
  )


def MFX_CAR_L4_SHAKER(name: str) -> MFXCarrier:
  """ Hamilton cat. no.: 187001
  Sometimes referred to as "PLT_CAR_L4_SHAKER" by Hamilton.
  Template carrier with 4 positions for Hamilton Heater Shaker.
  Occupies 7 tracks (7T). Can be screwed onto the deck.
  """
  return MFXCarrier(
    name=name,
    size_x=157.5,
    size_y=497.0,
    size_z=8.0,
    sites=create_homogeneous_carrier_sites(klass=CarrierSite, locations=[
        Coordinate(6.0, 2, 8.0), # not tested, interpolated Coordinate
        Coordinate(6.0, 123, 8.0), # not tested, interpolated Coordinate
        Coordinate(6.0, 244.0, 8.0), # tested using Hamilton_HC
        Coordinate(6.0, 365.0, 8.0), # tested using Hamilton_HS
      ],
      site_size_x=145.5,
      site_size_y=104.0,
    ),
    model="PLT_CAR_L4_SHAKER"
  )
