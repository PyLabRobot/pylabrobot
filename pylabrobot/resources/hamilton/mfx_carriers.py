import warnings
from typing import Dict

from pylabrobot.resources.carrier import (
  Coordinate,
  MFXCarrier,
  ResourceHolder,
)


def hamilton_mfx_carrier_L5_base(name: str, modules: Dict[int, ResourceHolder]) -> MFXCarrier:
  """Hamilton cat. no.: 188039
  Hamilton name: 'MFX_CAR_L5_base'
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
  Template carrier with 4 positions for Hamilton Heater Shaker in landscape.
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


def MFX_CAR_P3_SHAKER(name: str, modules: Dict[int, ResourceHolder]) -> MFXCarrier:
  """Hamilton cat. no.: 187001
  Sometimes referred to as "PLT_CAR_L4_SHAKER" by Hamilton, this one has extra holes for portrait orientation shakers.
    (you can drill these yourself, if you're adventurous)
  Some but not all of these carriers have:
    - extra holes for 3 portrait positions
    - extra holes for mounting a thermocycler
  This is a template carrier for setups with 3 positions for Hamilton Heater Shakers in portrait.
  Occupies 7 tracks (7T). Can be screwed onto the deck.
  Tested with hamilton heated shaker: HeaterShaker(size_x=146.2, size_y=103.6, size_z=74.11, child_location=Coordinate(x=10, y=13, z=74.24))
  """
  locations = [
    Coordinate(26.45, 0, 8.0),
    Coordinate(26.45, 146.2 + 17.2, 8.0),
    Coordinate(26.45, (146.2) * 2 + 17.2 + 11.6, 8.0),
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
    model="MFX_CAR_P3_SHAKER",
  )


def MFX_CAR_P3_base(name: str, modules: Dict[int, ResourceHolder]) -> MFXCarrier:
  """Hamilton cat. no.: 188053
  Labware carrier base for up to 3 Multiflex Modules in Portrait orientation
  Does not support half-indices
  Occupies 5 tracks (5T)
  """
  locations = [
    Coordinate(16.6, 35.2, 18.195),
    Coordinate(16.6, 179.2, 18.195),
    Coordinate(16.6, 325.2, 18.195),
  ]
  sites: Dict[int, ResourceHolder] = {}
  for i, module in modules.items():
    module.location = locations[i]
    sites[i] = module

  return MFXCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=18.195,
    sites=sites,
    model="MFX_CAR_P3_base",
  )

# Deprecated names for backwards compatibility
# TODO: Remove >2026-02

def MFX_CAR_L5_base(name: str, modules: Dict[int, ResourceHolder]) -> MFXCarrier:
  """Deprecated alias for `hamilton_mfx_carrier_L5_base`."""
  warnings.warn(
    "MFX_CAR_L5_base is deprecated. Use 'hamilton_mfx_carrier_L5_base' instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_mfx_carrier_L5_base(name, modules)
