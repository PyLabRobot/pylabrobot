from __future__ import annotations

from pylabrobot.resources.carrier import Coordinate, PlateHolder
from pylabrobot.resources.resource_holder import ResourceHolder

################## 1. Static modules ##################


def MFX_TIP_module(name: str) -> ResourceHolder:
  """Hamilton cat. no.: 188160
  Module to position a high-, standard-, low volume or 5ml tip rack (but not a 384 tip rack).
  """

  # resource_size_x=122.4,
  # resource_size_y=82.6,

  return ResourceHolder(
    name=name,
    size_x=135.0,
    size_y=94.0,
    size_z=214.8 - 18.195 - 100,
    # probe height - carrier_height - deck_height
    child_location=Coordinate(6.2, 5.0, 214.8 - 18.195 - 100),
    model="MFX_TIP_module",
  )


def MFX_DWP_rackbased_module(name: str) -> PlateHolder:
  """Hamilton cat. no.: 188229
  Module to position a Deep Well Plate / tube racks (MATRIX or MICRONICS) / NUNC reagent trough.
  """

  # resource_size_x=127.76,
  # resource_size_y=85.48,

  return PlateHolder(
    name=name,
    size_x=135.0,
    size_y=94.0,
    size_z=178.0 - 18.195 - 100,
    # probe height - carrier_height - deck_height
    child_location=Coordinate(4.0, 3.5, 178.0 - 18.195 - 100),
    model=MFX_DWP_rackbased_module.__name__,
    pedestal_size_z=-4.74,
  )
