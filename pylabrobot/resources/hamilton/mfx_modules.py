import warnings

from pylabrobot.resources.carrier import Coordinate, PlateHolder
from pylabrobot.resources.tip_rack_holder import EmbeddedTipRackHolder


def hamilton_mfx_tiprackholder_standard(name: str) -> EmbeddedTipRackHolder:
  """Hamilton cat. no.: 188160
  Hamilton name: 'MFX_TIP_module'
  Module to position a high-, standard- or low volume tip rack (but not a 384 tip rack).

  Takes an `EmbeddedTipRack` - Hamilton calls these 'framed' tip racks - which sinks into the
  module's opening and is centred over it, as in a tip carrier's site.
  """

  # The rack stands on the module's top: Hamilton's base for the module, 114.7 mm above the
  # carrier's base, less the carrier.
  top = 114.7 - 18.195

  return EmbeddedTipRackHolder(
    name=name,
    size_x=135.0,
    size_y=94.0,
    size_z=top,
    # Only the height: the holder centres a framed rack in X and Y and sinks it into its opening.
    child_location=Coordinate(x=0.0, y=0.0, z=top),
    model=hamilton_mfx_tiprackholder_standard.__name__,
  )


def MFX_TIP_module(name: str) -> EmbeddedTipRackHolder:
  """Deprecated: use `hamilton_mfx_tiprackholder_standard`."""
  warnings.warn(
    "MFX_TIP_module is deprecated and will be removed in the future. "
    "Use 'hamilton_mfx_tiprackholder_standard' instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_mfx_tiprackholder_standard(name=name)


def hamilton_mfx_plateholder_DWP_flat(name: str) -> PlateHolder:
  """Hamilton cat. no.: 188229
  Hamilton name: 'MFX_DWP_rackbased_module'
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
    model=hamilton_mfx_plateholder_DWP_flat.__name__,
    pedestal_size_z=0,
  )


def hamilton_mfx_plateholder_DWP_metal_tapped(name: str) -> PlateHolder:
  """Hamilton MFX DWP Module (cat.-no. 188042 / 188042-00).
  Hamilton name: 'MFX_DWP_rackbased_module'
  It also contains metal clamps at the corners.
  https://www.hamiltoncompany.com/other-robotics/188042
  """

  return PlateHolder(
    name=name,
    size_x=135.0,  # measured
    size_y=94.0,  # measured
    size_z=76.4,  # measured
    # probe height - carrier_height - deck_height
    child_location=Coordinate(4.0, 4.0, 183.95 - 18.195 - 100),  # measured
    pedestal_size_z=-4.74,
    model=hamilton_mfx_plateholder_DWP_metal_tapped.__name__,
  )


def MFX_DWP_module_flat(name: str) -> PlateHolder:
  """Hamilton cat. no.: 6601988-01
  Hamilton name: 'MFX_DWP_module_flat'
  Module to position a Deep Well Plate. Flat, metal base; no metal clamps.
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
    model=MFX_DWP_module_flat.__name__,
    pedestal_size_z=0,
  )


# Deprecated names for backwards compatibility
# TODO: Remove >2026-02


def Hamilton_MFX_plateholder_DWP_metal_tapped(name: str) -> PlateHolder:
  """Deprecated alias for `hamilton_mfx_plateholder_DWP_metal_tapped`."""
  warnings.warn(
    "Hamilton_MFX_plateholder_DWP_metal_tapped is deprecated. Use 'hamilton_mfx_plateholder_DWP_metal_tapped' instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_mfx_plateholder_DWP_metal_tapped(name)


def MFX_DWP_rackbased_module(name: str) -> PlateHolder:
  """Deprecated alias for `hamilton_mfx_plateholder_DWP_flat`."""
  warnings.warn(
    "MFX_DWP_rackbased_module is deprecated. Use 'hamilton_mfx_plateholder_DWP_flat' instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_mfx_plateholder_DWP_flat(name)
