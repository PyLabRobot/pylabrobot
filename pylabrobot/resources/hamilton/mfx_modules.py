from pylabrobot.resources.carrier import Coordinate, PlateHolder
from pylabrobot.resources.resource_holder import ResourceHolder


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
    size_z=178.0 - 18.195 - 100,  # 59.81mm
    # probe height - carrier_height - deck_height
    child_location=Coordinate(4.0, 3.5, 178.0 - 18.195 - 100),
    model=MFX_DWP_rackbased_module.__name__,
    pedestal_size_z=0,
  )


def MFX_DWP_module_188042(name: str) -> PlateHolder:
  """Hamilton MFX DWP Module (cat.-no. 188042 / 188042-00).
  This module contains a pedestal that elevates the plate by 6mm. The plate rests on the pedestal
  It also contains metal clamps at the corners.
  https://www.hamiltoncompany.com/other-robotics/188042
  """
  # ── module footprint ──────────────
  # size_x = 132.50          # mm
  size_x = 135
  # size_y = 92.05           # mm
  size_y = 94

  # ── vertical geometry ──────────────────────────────
  pedestal_size_z = 6.45  # The height of the pedestal
  # size_z = 178.3 - 18.195 - 100   # 60.10mm is module height above MFX carrier. This height does not including pedestal
  size_z = (
    184.745 - 18.195 - 100
  )  # 60.10mm is module height above MFX carrier. This height includes pedestal.
  # module.get_absolute_size_y() = 184.745 puts channel right on the pedestal

  # ── where the plate’s lower-left corner sits ───────
  child_x = 4.0  # mm
  child_y = 3.5  # mm
  child_location = Coordinate(child_x, child_y, size_z)

  return PlateHolder(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    child_location=child_location,
    model=MFX_DWP_module_188042.__name__,
    pedestal_size_z=pedestal_size_z,
  )


def MFX_DWP_module_flat(name: str) -> PlateHolder:
  """Hamilton cat. no.: 6601988-01
  Module to position a Deep Well Plate. Flat, metal base; no metal clamps like
  MFX_DWP_rackbased_module.
  Grey plastic corner clips secure plate. Plates rest on corners,
  rather than pedestal, so pedestal_size_z=0,
  """

  width = 134.0
  length = 92.10

  return PlateHolder(
    name=name,
    size_x=width,
    size_y=length,
    size_z=66.4,  # measured with caliper
    child_location=Coordinate(x=(width - 127.76) / 2, y=(length - 85.48) / 2, z=66.4),
    model=MFX_DWP_rackbased_module.__name__,
    pedestal_size_z=0,
  )
