from typing import Dict

from pylabrobot.resources.carrier import (
  Coordinate,
  MFXCarrier,
  ResourceHolder,
)


def MFX_CAR_L5_base(name: str, modules: Dict[int, ResourceHolder]) -> MFXCarrier:
  """Hamilton cat. no.: 188039
  Labware carrier base for up to 5 Multiflex Modules
  """
  locations = [
    Coordinate(0.0, 5.0, 18.195),
    Coordinate(0.0, 101.0, 18.195),
    Coordinate(0.0, 197.0, 18.195),
    Coordinate(0.0, 293.0, 18.195),
    Coordinate(0.0, 389.0, 18.195),
  ]
  half_locations = [c + Coordinate(y=90 / 2) for c in locations[:-1]]
  sites: Dict[int, ResourceHolder] = {}
  for i, module in modules.items():
    if isinstance(i, int):
      module.location = locations[i]
    elif i - int(i) == 0.5:
      module.location = half_locations[int(i)]
    else:
      raise ValueError(f"Invalid site index: {i}")

    sites[i] = module

  return MFXCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=18.195,
    sites=sites,
    model="MFX_CAR_L5_base",
  )


def MFX_CAR_L4_SHAKER(name: str, modules: Dict[int, ResourceHolder]) -> MFXCarrier:
  """Hamilton cat. no.: 187001
  Sometimes referred to as "PLT_CAR_L4_SHAKER" by Hamilton.
  Template carrier with 4 positions for Hamilton Heater Shaker.
  Occupies 7 tracks (7T). Can be screwed onto the deck.
  """
  locations = [
    Coordinate(6.0, 2, 8.0),  # not tested, interpolated Coordinate
    Coordinate(6.0, 123, 8.0),  # not tested, interpolated Coordinate
    Coordinate(6.0, 244.0, 8.0),  # tested using Hamilton_HC
    Coordinate(6.0, 365.0, 8.0),  # tested using Hamilton_HS
  ]
  sites: Dict[int, ResourceHolder] = {}
  for i, module in modules.items():
    module.location = locations[i]
    sites[i] = module

  return MFXCarrier(
    name=name,
    size_x=157.5,
    size_y=497.0,
    size_z=8.0,
    sites=sites,
    model="PLT_CAR_L4_SHAKER",
  )
